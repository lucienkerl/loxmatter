# loxmatter - bindet Matter-Geraete an einen Loxone Miniserver an.
# Copyright (C) 2026 Lucien Kerl
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Der aktive Thread-Datensatz, gelesen aus dem Border Router.

**Warum es dieses Modul gibt.** matter-server haelt die Thread-Zugangsdaten
ausschliesslich im Arbeitsspeicher: `_thread_credentials_set: bool = False`
im Konstruktor von `matter_server/server/device_controller.py`, auf `True`
gesetzt allein durch `set_thread_operational_dataset()`. Nichts davon wird
je auf die Platte geschrieben - im Datenverzeichnis des Dienstes
(`vendor_info`/`last_node_id`/`nodes`) steht kein Datensatz. Jeder Neustart
von matter-server loescht sie also, ohne dass irgendetwas es meldet.

Sichtbar wurde das am 2026-09-04: matter-server war am Vortag um 12:55 neu
gestartet, und seither scheiterte jedes Einlernen eines Thread-Geraets. Im
Log des Dienstes stand die Ursache im Klartext -

    Required network information not provided in commissioning parameters
    Parameters supplied: wifi (no) thread (no)
    Device supports: wifi (no) thread(yes)

- in der Oberflaeche dagegen nur "Commission with code failed for node 7".
BLE-Verbindung, Pairing-Code und die gesicherte Sitzung zum Geraet waren
allesamt in Ordnung; es fehlte einzig das Netz, in das das Geraet gehoert
haette. Die Oberflaeche hatte den Datensatz zwar als Eingabefeld, aber
optional und nach jedem Einlernen wieder geleert (`api/devices.py` schickte
ihn nur, wenn dort etwas stand) - solange matter-server durchlief, war das
harmlos, danach nicht mehr.

**Warum aus OTBR und nicht aus einem eigenen Speicher.** Der Border Router
ist die Stelle, an der der Datensatz ohnehin schon liegt, und er ist die
einzige, die ihn nach einem Netzwechsel von selbst richtig hat. Ein zweiter,
in dieser Bruecke gespeicherter Datensatz waere ab dem naechsten `docker
compose down` von OTBR ohne Volume (siehe Kommentar bei `otbr-state` in
`deploy/testhost/docker-compose.yml`) stillschweigend falsch - und ein
falscher Datensatz scheitert spaeter und undurchsichtiger als gar keiner.

**Warum ueber HTTP und nicht ueber `ot-ctl`.** Genau dieselbe Begruendung wie
bei `_check_thread()` in `api/diagnostics.py`: dieser Dienst laeuft in einem
eigenen Container und hat keinen Zugriff auf den von OTBR. OTBRs
REST-Schnittstelle dagegen lauscht auf 127.0.0.1:8081 im Netzwerk-
Namensraum des Hosts, den beide mit `network_mode: host` teilen - gemessen
aus dem laufenden loxmatter-Container heraus (Status 200, 222 Hex-Zeichen),
nicht vermutet.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, Final

# OTBRs REST-Schnittstelle auf demselben Host. Nicht konfigurierbar ueber
# einen CLI-Schalter, sondern ueber die Umgebung (siehe `_base_url`): der
# Regelfall braucht keine Angabe, und ein Schalter waere ein weiterer
# uebersetzter Hilfetext fuer eine Einstellung, die in diesem Stack nie
# jemand setzt.
DEFAULT_OTBR_URL: Final = "http://127.0.0.1:8081"

_OTBR_URL_ENV: Final = "LOXMATTER_OTBR_URL"

# Der Pfad ist Teil von OTBRs REST-API, nicht von uns gewaehlt.
_ACTIVE_DATASET_PATH: Final = "/node/dataset/active"

# Ohne diesen Header antwortet OTBR mit einer JSON-Struktur (Kanal, PAN-ID,
# Schluessel als Einzelfelder); `set_thread_operational_dataset` nimmt aber
# nur das Hex-TLV entgegen, das `text/plain` liefert.
_PLAIN_TEXT: Final = {"Accept": "text/plain"}

# Der Border Router steht im selben Haus, meist auf demselben Rechner - eine
# Antwort, die laenger braucht, kommt nicht mehr.
_TIMEOUT_SECONDS: Final = 5.0

_HEX_DIGITS: Final = frozenset("0123456789abcdefABCDEF")


class ThreadDatasetUnavailableError(RuntimeError):
    """Der Border Router konnte keinen aktiven Thread-Datensatz nennen.

    Kein Grund, das Einlernen abzubrechen: ein WiFi-Geraet braucht gar
    keinen (siehe `api/devices.py`, wo dieser Fehler zu einem Hinweis wird
    und nicht zu einem Abbruch). Fuer ein Thread-Geraet dagegen ist es die
    Ursache, die sonst erst 40 Sekunden spaeter als "Commission with code
    failed" ankommt - deshalb traegt die Ausnahme den Grund im Klartext.
    """


def _base_url() -> str:
    """Zur Aufrufzeit gelesen, nicht beim Import: sonst waere die Adresse in
    Tests nur noch ueber ein Neuladen des Moduls zu beeinflussen."""
    return os.environ.get(_OTBR_URL_ENV) or DEFAULT_OTBR_URL


def _default_session_factory() -> Any:
    # Lazy importiert wie in `matter/client.py`: Tests mit einer eigenen
    # Sitzung sollen aiohttp nie laden muessen.
    import aiohttp

    return aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS))


def validated_dataset(body: str, url: str) -> str:
    """Prueft eine Zeichenkette darauf, ob sie ein Thread-Datensatz sein kann.

    Oeffentlich, nicht modulprivat: zwei Aufrufer, eine Regel -
    `fetch_active_dataset` unten mit der Antwort des Border Routers, und
    `api/devices.py` mit dem von Hand in die Oberflaeche eingetragenen
    Datensatz. Eine zweite, nur aehnliche Pruefung dort waere genau die
    Doppelung, die frueher oder spaeter auseinanderlaeuft.

    `url` benennt in den Fehlermeldungen allein die HERKUNFT der geprueften
    Zeichenkette und ist deshalb nicht zwingend eine Adresse; die Route in
    `api/devices.py` formuliert ihre Meldung an den Bedienenden ohnehin
    selbst. Der Datensatz selbst taucht in KEINER Meldung dieser Funktion
    auf - er enthaelt den Netzwerkschluessel des Thread-Netzes.
    """
    dataset = body.strip()
    if not dataset:
        raise ThreadDatasetUnavailableError(
            f"{url} hat einen leeren Thread-Datensatz geliefert - hat der Border Router "
            "ein Netz gebildet? (`ot-ctl state` sollte `leader` oder `router` sagen)"
        )
    if not set(dataset) <= _HEX_DIGITS:
        # Zeigt bewusst NICHT die Antwort selbst: waere sie doch ein
        # Datensatz, stuende damit ein Credential im Log.
        raise ThreadDatasetUnavailableError(
            f"{url} hat keinen Thread-Datensatz geliefert, sondern {len(dataset)} Zeichen, "
            "die kein Hex sind - antwortet dort wirklich ein Border Router?"
        )
    return dataset


async def fetch_active_dataset(
    base_url: str | None = None,
    *,
    session_factory: Callable[[], Any] | None = None,
) -> str:
    """Holt den aktiven Thread-Datensatz als Hex-TLV vom Border Router.

    Der Rueckgabewert ist ein Credential - der Netzwerkschluessel des
    Thread-Netzes steckt darin. Er gehoert weder in ein Log noch in eine
    Fehlermeldung (siehe `deploy/testhost/README.md`); die Ausnahmen dieses
    Moduls nennen deshalb nur Adresse, Status und Laenge.
    """
    url = (base_url or _base_url()).rstrip("/") + _ACTIVE_DATASET_PATH
    session = (session_factory or _default_session_factory)()
    try:
        try:
            async with session.get(url, headers=dict(_PLAIN_TEXT)) as response:
                status = response.status
                body = await response.text()
        except Exception as exc:
            # `Exception` und nicht nur `aiohttp.ClientError`: dieses Modul
            # importiert aiohttp bewusst nicht selbst (siehe
            # `_default_session_factory`), und ein nicht erreichbarer Border
            # Router ist fuer den Aufrufer derselbe Fall wie ein
            # antwortender ohne Netz - ein Grund, kein Absturz.
            raise ThreadDatasetUnavailableError(
                f"{url} ist nicht erreichbar ({exc}) - laeuft der Border Router auf "
                "diesem Host, und ist seine REST-Schnittstelle aktiv?"
            ) from exc
    finally:
        await session.close()

    if status != 200:
        raise ThreadDatasetUnavailableError(
            f"{url} hat mit HTTP {status} geantwortet statt mit einem Thread-Datensatz - "
            "OTBR antwortet so, solange kein aktives Netz existiert."
        )
    return validated_dataset(body, url)

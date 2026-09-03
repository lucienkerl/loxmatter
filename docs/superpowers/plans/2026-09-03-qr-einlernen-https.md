# Einlernen per QR-Code samt HTTPS — Umsetzungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein neues Matter-Gerät lässt sich in der WebUI per QR-Code einlernen — über einen Live-Sucher auf einem neuen HTTPS-Listener, und ersatzweise über ein Foto, das auch ohne Zertifikat trägt.

**Architecture:** Ein neues Modul `loxmatter.tls` erzeugt und pflegt eine lokale CA samt Server-Zertifikat. `cli._run` startet daraufhin zwei `uvicorn.Server` auf **derselben** `build_app`-Instanz (HTTP wie bisher, HTTPS neu). Die Oberfläche dekodiert QR-Codes ausschließlich im Browser mit einer vendorten jsQR-Kopie und reicht den fertigen String an die unveränderte Route `POST /api/devices/commission`.

**Tech Stack:** Python 3.12, FastAPI, uvicorn 0.52.4, `cryptography` (neu), pytest/pytest-asyncio, Alpine.js 3.17.1 (vendort), jsQR 1.4.0 (vendort, neu).

**Entwurf:** [`docs/superpowers/specs/2026-09-03-qr-einlernen-https-design.md`](../specs/2026-09-03-qr-einlernen-https-design.md). Abschnittsnummern in diesem Plan verweisen dorthin.

## Global Constraints

- **Sprache:** Bezeichner Englisch, jeder Text, der vor einer Person landet, Deutsch — auch in JavaScript. Gilt für Fehlermeldungen, Platzhalter und Log-Ausgaben.
- **Kein Netzverweis in `index.html`.** [`tests/api/test_web.py:71`](../../../tests/api/test_web.py) prüft `"cdn." not in page` und `"unpkg" not in page`. Der Herkunftskommentar für jsQR nennt jsDelivr deshalb **nur beim Namen**, niemals als Hostname — genau wie der bestehende Alpine-Kommentar.
- **Kein Token in einer URL.** Weder Query-Parameter noch Fragment. [`tests/api/test_web.py:138`](../../../tests/api/test_web.py) prüft das gegen `app.js`.
- **Kein `<a href="/api…">`.** [`tests/api/test_web.py:128`](../../../tests/api/test_web.py) prüft `'href="/api' not in page`. `/ca.crt` liegt außerhalb von `/api` und darf deshalb ein gewöhnlicher Link sein.
- **Die Wörter `szene`, `zeitplan`, `automatisierung`, `favorit`** dürfen weder in `index.html` noch in `app.js` vorkommen ([`test_web.py:87`](../../../tests/api/test_web.py)).
- **TLS darf den Start nie verhindern.** Jeder Fehler bei der Zertifikatserzeugung endet in einer Log-Warnung und einem HTTP-only-Dienst, nie in einem Abbruch.
- **`/cmd` und `/resync` bleiben unverändert** und ungeschützt auf HTTP.
- **Zertifikatslaufzeiten:** CA 3650 Tage, Server 397 Tage. Signatur SHA-256, Schlüssel RSA-2048 (Kompatibilität vor Erzeugungsgeschwindigkeit — der Preis wird einmal beim Start bezahlt).
- **Ports:** HTTP `--listen` (Vorgabe 8080), HTTPS `--https-port` (Vorgabe 8443, `0` schaltet ab).
- **Dateinamen im TLS-Verzeichnis:** `ca.crt`, `ca.key`, `server.crt`, `server.key`. Beide `.key` mit Modus `0600`.
- **Testlauf:** `uv run pytest` läuft ohne Hardware und ohne Netzwerk. Das muss so bleiben.

## File Structure

| Datei | Verantwortung |
|---|---|
| `src/loxmatter/tls.py` (neu) | Zertifikatsmaterial: lokale Adressen ermitteln, CA und Server-Zertifikat erzeugen/erneuern, Zustand als `TlsState` melden. Kennt weder FastAPI noch uvicorn. |
| `src/loxmatter/loxone/server.py` (ändern) | Nimmt `TlsState` entgegen, liefert `GET /ca.crt` token-frei aus, reicht den Zustand an den Diagnose-Router weiter. |
| `src/loxmatter/api/diagnostics.py` (ändern) | `TlsStatusOut` und `GET /api/diagnostics/tls` — geschützt wie jede `/api`-Route. |
| `src/loxmatter/cli.py` (ändern) | Neue Optionen, `prepare_tls` aufrufen, zwei Server starten und geordnet beenden. |
| `src/loxmatter/web/vendor/jsqr.js` (neu) | Vendorte QR-Dekodierung. |
| `src/loxmatter/web/app.js` (ändern) | QR-Zustand, Dekodierpfad, Sucher, Origin-Wechsel, TLS-Zustand laden. |
| `src/loxmatter/web/index.html` (ändern) | Scan-Knopf, Bildweg, Sucher-Kasten, TLS-Abschnitt in Ansicht 4. |
| `src/loxmatter/web/style.css` (ändern) | Sucher und Ablagefeld. |
| `pyproject.toml` (ändern) | `cryptography>=42`. |
| `README.md` (ändern) | HTTPS, CA-Installation, die benannte Schwäche aus Abschnitt 5. |
| `tests/test_tls.py` (neu) | Zertifikatserzeugung und -erneuerung. |
| `tests/api/test_tls_routes.py` (neu) | `/ca.crt` und `/api/diagnostics/tls`. |
| `tests/test_cli_tls.py` (neu) | Optionen, Konfigurationsbau, geordnetes Beenden beider Server. |
| `tests/api/test_web.py` (ändern) | Markup-Stichproben für Scan-Knopf, Bildweg, jsQR, TLS-Abschnitt. |

---

### Task 1: Zertifikatsmaterial (`loxmatter.tls`)

**Files:**
- Create: `src/loxmatter/tls.py`
- Create: `tests/test_tls.py`
- Modify: `pyproject.toml` (Abhängigkeitsliste, nach `"aiohttp>=3.9",`)

**Interfaces:**
- Consumes: nichts aus früheren Tasks.
- Produces:
  - `class TlsUnavailableError(RuntimeError)`
  - `@dataclass(frozen=True) class TlsMaterial` mit `ca_certificate: Path`, `certificate: Path`, `private_key: Path`, `addresses: tuple[str, ...]`, `not_valid_after: datetime.datetime`
  - `@dataclass(frozen=True) class TlsState` mit `material: TlsMaterial | None`, `port: int | None`, `error: str | None`
  - `def local_ipv4_addresses() -> list[str]`
  - `def _filter_addresses(found: set[str]) -> list[str]`
  - `def ensure_tls_material(tls_dir: Path) -> TlsMaterial` (wirft `TlsUnavailableError`)
  - `def prepare_tls(tls_dir: Path, port: int) -> TlsState` (wirft **nie**)
  - Modulkonstanten `CA_FILE`, `CA_KEY_FILE`, `SERVER_FILE`, `SERVER_KEY_FILE`, `CRYPTOGRAPHY_AVAILABLE`, `SERVER_VALID_DAYS`, `CA_VALID_DAYS`
  - `def _now() -> datetime.datetime` (Testnaht für Ablaufprüfungen)

- [ ] **Step 1: Abhängigkeit eintragen**

In `pyproject.toml`, in der Liste `dependencies`, direkt nach der Zeile `"aiohttp>=3.9",` einfügen:

```toml
    # Task 1, QR/HTTPS: Erzeugung der lokalen CA und des Server-Zertifikats
    # fuer den HTTPS-Listener (Entwurf Abschnitt 4.4/4.5). Ein `openssl`-
    # Aufruf als Alternative wurde verworfen: das Basisimage
    # `python:3.12-slim` garantiert das Binary nicht, und der Dockerfile
    # sagt ueber sich selbst, dass er nie gebaut wurde. `>=42`, weil erst
    # dort `Certificate.not_valid_after_utc` existiert - der zeitzonenechte
    # Ersatz fuer das inzwischen veraltete `not_valid_after`.
    "cryptography>=42",
```

Dann:

```bash
uv sync
```

Erwartet: `cryptography` wird installiert, keine Fehlermeldung.

- [ ] **Step 2: Den fehlschlagenden Test schreiben**

Create `tests/test_tls.py`:

```python
"""Tests fuer das Zertifikatsmaterial des HTTPS-Listeners (Entwurf 4.4/4.5).

Diese Datei prueft drei Zusagen, die der Entwurf ausdruecklich macht: dass
das Server-Zertifikat die Adressen abdeckt, unter denen der Dienst
tatsaechlich erreichbar ist; dass es sich erneuert, wenn es das nicht mehr
tut (Ablauf oder neue DHCP-Adresse), waehrend die CA das ueberlebt; und dass
ein Fehlschlag nie als Ausnahme nach draussen dringt, weil TLS den Start
nicht verhindern darf.

`_now` in `loxmatter.tls` existiert allein als Naht fuer den Ablauftest -
ohne sie muesste dieser Test 397 Tage warten oder ein Zertifikat mit
absichtlich kaputter Laufzeit erzeugen, was etwas anderes pruefte als den
echten Pfad.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pytest
from cryptography import x509

from loxmatter import tls
from loxmatter.tls import TlsUnavailableError, ensure_tls_material, prepare_tls


def _certificate(path: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(path.read_bytes())


def _serial(path: Path) -> int:
    return _certificate(path).serial_number


def _san(path: Path) -> dict[str, list[str]]:
    extension = _certificate(path).extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    )
    return {
        "dns": list(extension.value.get_values_for_type(x509.DNSName)),
        "ip": [str(value) for value in extension.value.get_values_for_type(x509.IPAddress)],
    }


def test_a_fresh_directory_gets_a_ca_and_a_server_certificate(tmp_path):
    material = ensure_tls_material(tmp_path)

    assert material.ca_certificate == tmp_path / "ca.crt"
    assert material.certificate == tmp_path / "server.crt"
    assert material.private_key == tmp_path / "server.key"
    for path in (material.ca_certificate, material.certificate, material.private_key):
        assert path.exists()


def test_the_ca_is_a_ca_and_the_server_certificate_is_not(tmp_path):
    ensure_tls_material(tmp_path)

    ca_constraints = _certificate(tmp_path / "ca.crt").extensions.get_extension_for_class(
        x509.BasicConstraints
    )
    server_constraints = _certificate(tmp_path / "server.crt").extensions.get_extension_for_class(
        x509.BasicConstraints
    )
    assert ca_constraints.value.ca is True
    assert server_constraints.value.ca is False


def test_the_server_certificate_is_signed_by_the_ca(tmp_path):
    ensure_tls_material(tmp_path)

    assert _certificate(tmp_path / "server.crt").issuer == _certificate(tmp_path / "ca.crt").subject


@pytest.mark.skipif(os.getuid() == 0, reason="root umgeht Dateirechte")
def test_the_private_keys_are_readable_by_nobody_else(tmp_path):
    """Ein Schluessel, den jeder Nutzer der Maschine lesen kann, ist kein
    Schluessel - und das TLS-Verzeichnis liegt neben der Datenbank, also an
    einer Stelle, an der auch andere Dinge liegen."""
    ensure_tls_material(tmp_path)

    for name in ("ca.key", "server.key"):
        assert (tmp_path / name).stat().st_mode & 0o077 == 0


def test_the_certificate_always_covers_localhost(tmp_path):
    ensure_tls_material(tmp_path)
    names = _san(tmp_path / "server.crt")

    assert "localhost" in names["dns"]
    assert "127.0.0.1" in names["ip"]
    assert "::1" in names["ip"]


def test_the_certificate_covers_every_address_it_was_given(tmp_path, monkeypatch):
    """Die Adressliste wird hier FEST vorgegeben, statt die echte zu
    benutzen: `local_ipv4_addresses()` liefert in einer Sandbox ohne
    Netzwerk eine leere Liste, und eine Schleife darueber liefe leer durch -
    ein gruener Test, der nichts geprueft hat. Die Suite soll laut README
    ohne Netzwerkzugriff laufen, also darf ihr Ergebnis nicht davon
    abhaengen, ob gerade ein Kabel steckt."""
    monkeypatch.setattr(tls, "local_ipv4_addresses", lambda: ["192.168.178.42", "10.0.0.7"])

    material = ensure_tls_material(tmp_path)
    names = _san(tmp_path / "server.crt")

    assert material.addresses == ("192.168.178.42", "10.0.0.7")
    assert "192.168.178.42" in names["ip"]
    assert "10.0.0.7" in names["ip"]


def test_a_second_call_reuses_what_is_already_there(tmp_path):
    """Sonst bekaeme jeder Neustart ein neues Zertifikat - und jedes Handy
    eine neue Warnung, obwohl sich nichts geaendert hat."""
    ensure_tls_material(tmp_path)
    ca_serial = _serial(tmp_path / "ca.crt")
    server_serial = _serial(tmp_path / "server.crt")

    ensure_tls_material(tmp_path)

    assert _serial(tmp_path / "ca.crt") == ca_serial
    assert _serial(tmp_path / "server.crt") == server_serial


def test_a_new_address_renews_the_certificate_but_keeps_the_ca(tmp_path, monkeypatch):
    """Der DHCP-Fall. Wuerde die CA mitrotieren, waere das auf dem Handy
    eingerichtete Vertrauen nach jedem Router-Neustart wertlos (Entwurf 4.4)."""
    ensure_tls_material(tmp_path)
    ca_serial = _serial(tmp_path / "ca.crt")
    server_serial = _serial(tmp_path / "server.crt")

    monkeypatch.setattr(tls, "local_ipv4_addresses", lambda: ["10.9.9.9"])
    ensure_tls_material(tmp_path)

    assert _serial(tmp_path / "ca.crt") == ca_serial
    assert _serial(tmp_path / "server.crt") != server_serial
    assert "10.9.9.9" in _san(tmp_path / "server.crt")["ip"]


def test_an_expired_certificate_is_renewed_but_the_ca_survives(tmp_path, monkeypatch):
    ensure_tls_material(tmp_path)
    ca_serial = _serial(tmp_path / "ca.crt")
    server_serial = _serial(tmp_path / "server.crt")

    later = dt.datetime.now(dt.UTC) + dt.timedelta(days=400)
    monkeypatch.setattr(tls, "_now", lambda: later)
    ensure_tls_material(tmp_path)

    assert _serial(tmp_path / "ca.crt") == ca_serial
    assert _serial(tmp_path / "server.crt") != server_serial


def test_the_server_certificate_does_not_outlive_apples_limit(tmp_path):
    """397 Tage, siehe Entwurf 4.4."""
    material = ensure_tls_material(tmp_path)

    assert material.not_valid_after - dt.datetime.now(dt.UTC) <= dt.timedelta(days=398)


def test_without_cryptography_the_error_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(tls, "CRYPTOGRAPHY_AVAILABLE", False)

    with pytest.raises(TlsUnavailableError, match="cryptography"):
        ensure_tls_material(tmp_path)


def test_prepare_tls_reports_a_failure_instead_of_raising(tmp_path, monkeypatch):
    """TLS darf den Start nicht verhindern (Entwurf 4.5)."""
    monkeypatch.setattr(tls, "CRYPTOGRAPHY_AVAILABLE", False)

    state = prepare_tls(tmp_path, 8443)

    assert state.material is None
    assert state.port is None
    assert state.error is not None
    assert "cryptography" in state.error


@pytest.mark.skipif(os.getuid() == 0, reason="root umgeht Dateirechte")
def test_prepare_tls_survives_an_unwritable_directory(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.mkdir(mode=0o500)

    state = prepare_tls(blocked / "tls", 8443)

    assert state.material is None
    assert state.error is not None


def test_port_zero_switches_https_off_without_an_error(tmp_path):
    state = prepare_tls(tmp_path, 0)

    assert state.material is None
    assert state.port is None
    assert state.error is None
    assert not (tmp_path / "ca.crt").exists()


def test_local_addresses_never_contain_loopback():
    """127.0.0.1 wird in `_build_server_certificate` ohnehin fest
    hinzugefuegt - stuende es zusaetzlich in dieser Liste, taeuschte der
    Vergleich in `ensure_tls_material` eine Aenderung vor, wo keine ist.

    Der Filter wird an einer FESTEN Eingabe geprueft, nicht am echten
    Netzwerk: ohne Netz ist die echte Liste leer, und eine Schleife darueber
    liefe leer durch. `_filter_addresses` ist genau deshalb eine eigene
    Funktion - sie ist der Teil von `local_ipv4_addresses`, der eine
    pruefbare Regel enthaelt."""
    found = {"127.0.0.1", "127.0.1.1", "192.168.178.42", "10.0.0.7"}

    assert tls._filter_addresses(found) == ["10.0.0.7", "192.168.178.42"]


def test_the_real_address_lookup_returns_no_loopback(tmp_path):
    """Dieselbe Regel am echten Aufruf. Er darf eine leere Liste liefern
    (kein Netz) - was er nicht darf, ist Loopback zurueckgeben."""
    for address in tls.local_ipv4_addresses():
        assert not address.startswith("127.")
```

- [ ] **Step 3: Test laufen lassen und Fehlschlag bestätigen**

Run: `uv run pytest tests/test_tls.py -v`
Expected: FAIL, alle Tests mit `ModuleNotFoundError: No module named 'loxmatter.tls'` (Sammelfehler beim Import).

- [ ] **Step 4: Das Modul schreiben**

Create `src/loxmatter/tls.py`:

```python
"""Zertifikatsmaterial fuer den HTTPS-Listener (Entwurf Abschnitt 4.4/4.5).

Dieses Modul kennt weder FastAPI noch uvicorn. Es beantwortet genau zwei
Fragen: unter welchen Adressen ist dieser Dienst erreichbar, und wo liegen
Zertifikat und Schluessel, die dafuer gelten. Wer sie benutzt, steht
woanders (`cli._run` baut daraus die zweite `uvicorn.Config`,
`loxone.server.build_app` liefert die CA aus).

**Warum ein Paar aus CA und Server-Zertifikat, nicht ein einzelnes
selbstsigniertes.** Ein Handy soll einmal etwas Dauerhaftes als
vertrauenswuerdig einrichten koennen. Waere das installierte Objekt das
Server-Zertifikat selbst, waere die Installation nach jedem Adresswechsel
hinfaellig - und Adresswechsel sind bei DHCP der Normalfall, nicht die
Ausnahme. Deshalb wird nur das SERVER-Zertifikat erneuert, wenn eine
Adresse dazukommt oder die Laufzeit endet; die CA ueberlebt das
ausdruecklich (siehe `ensure_tls_material` und
`test_a_new_address_renews_the_certificate_but_keeps_the_ca`).

**Warum RSA-2048 und nicht EC.** EC-Schluessel waeren schneller erzeugt.
Der Unterschied wird einmal beim Start bezahlt und ist dort belanglos,
waehrend ein Geraet, das mit dem Schluesseltyp nicht zurechtkommt, das
gesamte Feature verliert. Bei einer Wahl zwischen Millisekunden und
Kompatibilitaet gewinnt die Kompatibilitaet.

**Warum der Import von `cryptography` einen Schalter hat.** Der Dienst muss
auch dann starten, wenn das Paket fehlt (Entwurf 4.5) - dann eben ohne
HTTPS. Ein `ImportError` auf Modulebene wuerde stattdessen `cli.py` mit
herunterreissen, das dieses Modul unbedingt importiert.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import logging
import os
import socket
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:  # pragma: no cover - haengt an der Installation, nicht am Code
    CRYPTOGRAPHY_AVAILABLE = False

CA_FILE = "ca.crt"
CA_KEY_FILE = "ca.key"
SERVER_FILE = "server.crt"
SERVER_KEY_FILE = "server.key"

CA_VALID_DAYS = 3650
# 397 Tage: Apples 398-Tage-Grenze ist fuer Zertifikate belegt, die an eine im
# System mitgelieferte Wurzel anschliessen; ob sie auch fuer eine selbst
# installierte Wurzel greift, ist nicht belegt. Sie einzuhalten kostet nichts
# und nimmt die Frage aus dem Weg (Entwurf 4.4).
SERVER_VALID_DAYS = 397
# Wie lange vor dem Ablauf bereits erneuert wird - sonst laeuft ein
# Zertifikat mitten im Betrieb ab, statt bei einem Neustart.
RENEW_BEFORE_DAYS = 30

CA_COMMON_NAME = "loxmatter local CA"
SERVER_COMMON_NAME = "loxmatter"

# Eine Adresse aus dem Dokumentationsbereich (RFC 5737). An sie wird nichts
# gesendet - ein UDP-Socket "verbindet" sich ohne jedes Paket, und das
# Betriebssystem verraet dabei, ueber welche lokale Adresse es das taete.
_ROUTE_PROBE_ADDRESS = "192.0.2.1"


class TlsUnavailableError(RuntimeError):
    """HTTPS laesst sich in dieser Installation nicht einrichten.

    Traegt immer einen Grund, der einer Person weiterhilft - er landet
    woertlich im Log und in `GET /api/diagnostics/tls`."""


@dataclass(frozen=True)
class TlsMaterial:
    """Wo das gueltige Material liegt, und wofuer es gilt."""

    ca_certificate: Path
    certificate: Path
    private_key: Path
    addresses: tuple[str, ...]
    not_valid_after: dt.datetime


@dataclass(frozen=True)
class TlsState:
    """Was `cli` und die Oberflaeche ueber HTTPS wissen muessen.

    Alle drei Felder zusammen sind eindeutig: `material is None and error is
    None` heisst "abgeschaltet" (`--https-port 0`), `material is None and
    error is not None` heisst "gewollt, aber nicht moeglich" - zwei Faelle,
    die die Oberflaeche unterschiedlich erklaeren muss."""

    material: TlsMaterial | None
    port: int | None
    error: str | None


def _now() -> dt.datetime:
    """Eigene Funktion statt eines direkten Aufrufs: der Ablauftest in
    `tests/test_tls.py` haengt sich hier ein, statt 397 Tage zu warten."""
    return dt.datetime.now(dt.UTC)


def local_ipv4_addresses() -> list[str]:
    """Die IPv4-Adressen, unter denen dieser Rechner erreichbar sein duerfte.

    Zwei Quellen, vereinigt, weil keine allein traegt: der Routen-Test unten
    liefert zuverlaessig die Adresse, ueber die der Verkehr tatsaechlich
    laeuft, aber nur diese eine; `getaddrinfo` ueber den eigenen Rechnernamen
    findet weitere Schnittstellen, liefert auf manchen Debian-Systemen aber
    nur `127.0.1.1`. Loopback fliegt in beiden Faellen raus - `127.0.0.1`
    und `::1` kommen in `_build_server_certificate` ohnehin fest dazu, und
    stuenden sie zusaetzlich hier, taeuschte der Vergleich in
    `ensure_tls_material` eine Aenderung vor, wo keine ist.

    Bewusst ohne `netifaces`/`psutil`: eine weitere Abhaengigkeit fuer eine
    Liste, die im Fehlerfall lediglich eine Adresse weniger enthaelt - der
    Nutzer bekommt dann eine Zertifikatswarnung mehr, keinen Ausfall.
    """
    found: set[str] = set()

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((_ROUTE_PROBE_ADDRESS, 1))
        found.add(probe.getsockname()[0])
    except OSError:
        # Kein Netz, keine Route - dann bleibt es bei localhost.
        pass
    finally:
        probe.close()

    try:
        for entry in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(entry[4][0])
    except (OSError, socket.gaierror):
        pass

    return _filter_addresses(found)


def _filter_addresses(found: set[str]) -> list[str]:
    """Der pruefbare Teil von `local_ipv4_addresses`: Loopback raus, sortiert.

    Eigene Funktion, damit die Regel an einer festen Eingabe pruefbar ist -
    die echte Ermittlung liefert in einer Sandbox ohne Netz eine leere
    Menge, und ein Test darueber liefe leer durch."""
    return sorted(address for address in found if not address.startswith("127."))


def _write_private(path: Path, data: bytes) -> None:
    """Schreibt Schluesselmaterial, das nur der eigene Nutzer lesen kann.

    `os.open` mit Modus statt `write_bytes` plus `chmod`: sonst existierte
    die Datei fuer die Dauer eines Schreibvorgangs mit den Rechten, die die
    umask hergibt. Der `chmod` danach ist trotzdem noetig, weil `os.open`
    den Modus bei einer BEREITS vorhandenen Datei ignoriert."""
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "wb") as stream:
        stream.write(data)
    path.chmod(0o600)


def _new_key() -> "rsa.RSAPrivateKey":
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _key_bytes(key: "rsa.RSAPrivateKey") -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _build_ca(key: "rsa.RSAPrivateKey") -> "x509.Certificate":
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_COMMON_NAME)])
    now = _now()
    return (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        # Fuenf Minuten Vorlauf: eine leicht nachgehende Uhr auf dem Handy
        # wuerde ein Zertifikat sonst als "noch nicht gueltig" ablehnen.
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=CA_VALID_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )


def _build_server_certificate(
    ca_certificate: "x509.Certificate",
    ca_key: "rsa.RSAPrivateKey",
    key: "rsa.RSAPrivateKey",
    addresses: list[str],
) -> "x509.Certificate":
    names: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        x509.IPAddress(ipaddress.ip_address("::1")),
    ]
    names.extend(x509.IPAddress(ipaddress.ip_address(address)) for address in addresses)
    now = _now()
    return (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, SERVER_COMMON_NAME)]))
        .issuer_name(ca_certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=SERVER_VALID_DAYS))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )


def _load_certificate(path: Path) -> "x509.Certificate | None":
    try:
        return x509.load_pem_x509_certificate(path.read_bytes())
    except (OSError, ValueError):
        # Eine kaputte oder halb geschriebene Datei ist kein Grund
        # aufzugeben - sie wird gleich ueberschrieben.
        return None


def _ensure_ca(tls_dir: Path) -> tuple["x509.Certificate", "rsa.RSAPrivateKey"]:
    certificate_path = tls_dir / CA_FILE
    key_path = tls_dir / CA_KEY_FILE
    certificate = _load_certificate(certificate_path)
    if certificate is not None and key_path.exists():
        try:
            key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        except (OSError, ValueError):
            key = None
        if key is not None and certificate.not_valid_after_utc > _now():
            return certificate, key  # type: ignore[return-value]

    key = _new_key()
    certificate = _build_ca(key)
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    _write_private(key_path, _key_bytes(key))
    logger.info("Lokale CA erzeugt: %s", certificate_path)
    return certificate, key


def _server_is_current(
    path: Path, ca_certificate: "x509.Certificate", addresses: list[str]
) -> bool:
    certificate = _load_certificate(path)
    if certificate is None:
        return False
    if certificate.issuer != ca_certificate.subject:
        return False
    if certificate.not_valid_after_utc <= _now() + dt.timedelta(days=RENEW_BEFORE_DAYS):
        return False
    try:
        extension = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound:
        return False
    covered = {str(value) for value in extension.value.get_values_for_type(x509.IPAddress)}
    return set(addresses) <= covered


def ensure_tls_material(tls_dir: Path) -> TlsMaterial:
    """Sorgt dafuer, dass in `tls_dir` gueltiges Material liegt, und sagt wo.

    Erneuert wird nur, was erneuert werden muss: die CA ueberlebt einen
    Adresswechsel und einen Ablauf des Server-Zertifikats (siehe
    Moduldocstring). Wirft `TlsUnavailableError`, wenn das nicht geht -
    `prepare_tls` faengt das ab, damit der Dienst trotzdem startet."""
    if not CRYPTOGRAPHY_AVAILABLE:
        raise TlsUnavailableError(
            "das Paket cryptography ist nicht installiert - ohne es laesst sich "
            "kein Zertifikat erzeugen"
        )
    try:
        tls_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise TlsUnavailableError(
            f"das Verzeichnis {tls_dir} konnte nicht angelegt werden: {exc}"
        ) from exc

    addresses = local_ipv4_addresses()
    try:
        ca_certificate, ca_key = _ensure_ca(tls_dir)
        certificate_path = tls_dir / SERVER_FILE
        key_path = tls_dir / SERVER_KEY_FILE
        if not _server_is_current(certificate_path, ca_certificate, addresses):
            key = _new_key()
            certificate = _build_server_certificate(ca_certificate, ca_key, key, addresses)
            certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
            _write_private(key_path, _key_bytes(key))
            logger.info(
                "Server-Zertifikat erzeugt fuer %s", ", ".join(["localhost", *addresses])
            )
        else:
            certificate = _load_certificate(certificate_path)
            assert certificate is not None  # `_server_is_current` hat es eben gelesen
    except OSError as exc:
        raise TlsUnavailableError(
            f"das Zertifikat in {tls_dir} konnte nicht geschrieben werden: {exc}"
        ) from exc

    return TlsMaterial(
        ca_certificate=tls_dir / CA_FILE,
        certificate=certificate_path,
        private_key=key_path,
        addresses=tuple(addresses),
        not_valid_after=certificate.not_valid_after_utc,
    )


def prepare_tls(tls_dir: Path, port: int) -> TlsState:
    """Der eine Einstiegspunkt fuer `cli`. Wirft nie.

    TLS ist eine Zugabe; es darf die Loxone-Strecke nicht zum Stehen
    bringen (Entwurf 4.5). Jeder Fehler wird zu einer Warnung im Log und zu
    einem `TlsState` ohne Material - der Dienst laeuft dann wie bisher nur
    ueber HTTP, und `GET /api/diagnostics/tls` nennt den Grund."""
    if port == 0:
        return TlsState(material=None, port=None, error=None)
    try:
        material = ensure_tls_material(tls_dir)
    except TlsUnavailableError as exc:
        logger.warning(
            "HTTPS nicht verfuegbar: %s. Der Dienst laeuft nur ueber HTTP - "
            "der QR-Scan mit der Kamera bleibt dadurch gesperrt, der Weg "
            "ueber ein Foto funktioniert weiterhin.",
            exc,
        )
        return TlsState(material=None, port=None, error=str(exc))
    return TlsState(material=material, port=port, error=None)
```

- [ ] **Step 5: Tests laufen lassen**

Run: `uv run pytest tests/test_tls.py -v`
Expected: PASS, alle 16 Tests.

- [ ] **Step 6: Linter und Typprüfung**

```bash
uv run ruff check src/loxmatter/tls.py tests/test_tls.py && uv run ruff format --check src/loxmatter/tls.py tests/test_tls.py && uv run mypy src/loxmatter/tls.py
```

Erwartet: keine Fehler. Falls `ruff format --check` meckert, `uv run ruff format` auf beide Dateien anwenden und erneut prüfen.

- [ ] **Step 7: Committen**

```bash
git add pyproject.toml uv.lock src/loxmatter/tls.py tests/test_tls.py
git commit -m "feat(tls): lokale CA und Server-Zertifikat erzeugen

Nur das Server-Zertifikat wird erneuert, wenn eine DHCP-Adresse
dazukommt oder die Laufzeit endet - die CA ueberlebt das, sonst waere
das auf einem Handy eingerichtete Vertrauen nach jedem Router-Neustart
wertlos. prepare_tls wirft nie: TLS darf den Start nicht verhindern.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: `GET /ca.crt` und `GET /api/diagnostics/tls`

**Files:**
- Modify: `src/loxmatter/api/diagnostics.py` (Modell nach `SystemCheckOut` bei Zeile 196, Signatur `build_diagnostics_router` bei Zeile 328, neue Route im Router)
- Modify: `src/loxmatter/loxone/server.py` (Signatur `build_app` bei Zeile 289, Durchreichen bei Zeile 370, neue Route nach `/health` bei Zeile 402)
- Create: `tests/api/test_tls_routes.py`

**Interfaces:**
- Consumes: `loxmatter.tls.TlsState`, `TlsMaterial` (Task 1).
- Produces:
  - `class TlsStatusOut(BaseModel)` in `api/diagnostics.py` mit `enabled: bool`, `port: int | None`, `addresses: list[str]`, `expires: str | None`, `error: str | None`
  - `build_diagnostics_router(..., tls_state: TlsState | None = None)` — neuer Parameter **hinter** `api_token_configured`
  - `build_app(..., tls_state: TlsState | None = None)` — neuer Parameter hinter `api_token`
  - Route `GET /ca.crt` (token-frei) und `GET /api/diagnostics/tls` (geschützt)

- [ ] **Step 1: Den fehlschlagenden Test schreiben**

Create `tests/api/test_tls_routes.py`:

```python
"""Tests fuer die beiden TLS-Routen (Entwurf Abschnitt 5).

Der entscheidende Unterschied zwischen ihnen ist der Zugang, nicht der
Inhalt: `/ca.crt` muss OHNE Token und ueber HTTP erreichbar sein, weil man
es genau dann laedt, wenn man dem HTTPS-Zugang noch nicht vertraut -
waehrend `/api/diagnostics/tls` wie jede `/api`-Route geschuetzt bleibt.
Genau deshalb liegt `/ca.crt` ausserhalb von `/api`: jeder der fuenf
`/api`-Router haengt am Waechter, und eine token-freie Ausnahme darunter
waere eine Falle fuer die naechste Person, die einen Router ergaenzt.
"""

from __future__ import annotations

import httpx2 as httpx
import pytest
from conftest import load_snapshot

from loxmatter.export.commands import extract_commands
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store
from loxmatter.tls import prepare_tls


@pytest.fixture
def tls_state(tmp_path):
    return prepare_tls(tmp_path / "tls", 8443)


@pytest.fixture
async def client_factory(tmp_path, no_invoke, fake_runtime, fake_client):
    """Baut eine App mit frei waehlbarem TLS-Zustand und Token."""
    stores: list[Store] = []

    def build(tls_state=None, api_token=None):
        store = Store(tmp_path / f"t{len(stores)}.sqlite")
        stores.append(store)
        snapshot = load_snapshot("ikea_grillplats_plug.json")
        device_id = store.register_device(snapshot)
        store.register_signals(device_id, snapshot)
        store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)
        app = build_app(
            store,
            no_invoke,
            fake_runtime(store),
            client=fake_client,
            api_token=api_token,
            tls_state=tls_state,
        )
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )

    yield build
    for store in stores:
        store.close()


async def test_the_ca_certificate_is_served_without_a_token(client_factory, tls_state):
    """Man laedt sie, BEVOR man HTTPS vertraut - ein Token-Zwang hier
    machte die Route nutzlos, und zwar genau im einzigen Moment, in dem sie
    gebraucht wird."""
    async with client_factory(tls_state=tls_state, api_token="geheim") as client:
        response = await client.get("/ca.crt")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-x509-ca-cert")
    assert b"BEGIN CERTIFICATE" in response.content


async def test_the_ca_route_answers_503_when_https_is_off(client_factory):
    async with client_factory(tls_state=None) as client:
        response = await client.get("/ca.crt")

    assert response.status_code == 503


async def test_the_ca_route_serves_the_ca_not_the_server_certificate(client_factory, tls_state):
    """Wer das Server-Zertifikat installierte, muesste das nach jedem
    Adresswechsel wiederholen (Entwurf 4.4)."""
    async with client_factory(tls_state=tls_state) as client:
        served = (await client.get("/ca.crt")).content

    assert served == tls_state.material.ca_certificate.read_bytes()
    assert served != tls_state.material.certificate.read_bytes()


async def test_the_private_key_is_never_served(client_factory, tls_state):
    async with client_factory(tls_state=tls_state) as client:
        served = (await client.get("/ca.crt")).content

    assert b"PRIVATE KEY" not in served


async def test_the_status_route_needs_a_token(client_factory, tls_state):
    async with client_factory(tls_state=tls_state, api_token="geheim") as client:
        assert (await client.get("/api/diagnostics/tls")).status_code == 401
        response = await client.get(
            "/api/diagnostics/tls", headers={"Authorization": "Bearer geheim"}
        )

    assert response.status_code == 200


async def test_the_status_names_the_port_and_the_addresses(client_factory, tls_state):
    async with client_factory(tls_state=tls_state) as client:
        payload = (await client.get("/api/diagnostics/tls")).json()

    assert payload["enabled"] is True
    assert payload["port"] == 8443
    assert payload["error"] is None
    assert payload["expires"] is not None
    for address in tls_state.material.addresses:
        assert address in payload["addresses"]


async def test_the_status_distinguishes_switched_off_from_broken(client_factory):
    from loxmatter.tls import TlsState

    async with client_factory(tls_state=TlsState(material=None, port=None, error=None)) as client:
        switched_off = (await client.get("/api/diagnostics/tls")).json()
    broken = TlsState(material=None, port=None, error="kein Platz auf dem Datentraeger")
    async with client_factory(tls_state=broken) as client:
        failed = (await client.get("/api/diagnostics/tls")).json()

    assert switched_off["enabled"] is False
    assert switched_off["error"] is None
    assert failed["enabled"] is False
    assert failed["error"] == "kein Platz auf dem Datentraeger"
```

- [ ] **Step 2: Test laufen lassen und Fehlschlag bestätigen**

Run: `uv run pytest tests/api/test_tls_routes.py -v`
Expected: FAIL mit `TypeError: build_app() got an unexpected keyword argument 'tls_state'`.

- [ ] **Step 3: Modell und Route im Diagnose-Router ergänzen**

In `src/loxmatter/api/diagnostics.py`, nach der Klasse `SystemCheckOut` (endet vor Zeile 328) einfügen:

```python
class TlsStatusOut(BaseModel):
    """Zustand des HTTPS-Listeners (Entwurf Abschnitt 5).

    `enabled=False` mit `error=None` heisst "abgeschaltet" (`--https-port
    0`), `enabled=False` mit einem `error` heisst "gewollt, aber nicht
    moeglich" - die Oberflaeche muss beide unterschiedlich erklaeren, sonst
    sucht jemand einen Fehler, den er selbst konfiguriert hat, oder haelt
    einen echten Fehler fuer eine Einstellung.

    Der Pfad zum Herunterladen der CA steht hier bewusst NICHT drin: er ist
    fest `/ca.crt` und liegt ausserhalb von `/api`, damit ihn auch jemand
    ohne Token erreicht. Ihn hier zu melden erweckte den Eindruck, er sei
    beweglich."""

    model_config = ConfigDict(frozen=True)

    enabled: bool
    port: int | None
    addresses: list[str]
    expires: str | None
    error: str | None
```

Die Signatur bei Zeile 328 ergänzen (neuer Parameter am Ende):

```python
def build_diagnostics_router(
    store: Store,
    command_log: RingBuffer[CommandLogEntry],
    client: BridgeMatterClient | None,
    sender: UdpSender | None,
    matter_data_dir: Path | None,
    api_token_configured: bool = False,
    tls_state: TlsState | None = None,
) -> APIRouter:
```

Import oben ergänzen, direkt nach `from loxmatter.model.store import Store`:

```python
from loxmatter.tls import TlsState
```

Und im Router-Körper, nach der `/commands`-Route (Zeile 373 ff.), einfügen:

```python
    @router.get("/tls")
    async def tls_status() -> TlsStatusOut:
        """Sagt, ob HTTPS laeuft, unter welchen Adressen und wie lange noch.

        Geschuetzt wie jede `/api`-Route - anders als `GET /ca.crt`, das
        ohne Token auskommen MUSS (siehe `loxone.server`). Der Unterschied
        ist kein Versehen: dieser Zustand wird gelesen, wenn man bereits in
        der Oberflaeche ist, das Zertifikat dagegen, bevor man ihr ueber
        HTTPS ueberhaupt vertrauen kann."""
        if tls_state is None or tls_state.material is None:
            return TlsStatusOut(
                enabled=False,
                port=None,
                addresses=[],
                expires=None,
                error=None if tls_state is None else tls_state.error,
            )
        return TlsStatusOut(
            enabled=True,
            port=tls_state.port,
            addresses=list(tls_state.material.addresses),
            expires=tls_state.material.not_valid_after.isoformat(),
            error=None,
        )
```

- [ ] **Step 4: `build_app` erweitern**

In `src/loxmatter/loxone/server.py`, Signatur bei Zeile 289:

```python
def build_app(
    store: Store,
    invoke: Invoker,
    runtime: Runtime,
    client: BridgeMatterClient | None = None,
    sender: UdpSender | None = None,
    matter_data_dir: Path | None = None,
    api_token: str | None = None,
    tls_state: TlsState | None = None,
) -> FastAPI:
```

Import ergänzen, nach `from loxmatter.model.store import Store`:

```python
from loxmatter.tls import TlsState
```

Beim `build_diagnostics_router`-Aufruf (Zeile 370 ff.) hinter `api_token_configured=...` ergänzen:

```python
            tls_state=tls_state,
```

Und nach der `/health`-Route (Zeile 402-404) einfügen:

```python
    @app.get("/ca.crt", include_in_schema=False)
    async def ca_certificate() -> FileResponse:
        """Das CA-Zertifikat zum Installieren auf einem Handy (Entwurf 5).

        **Bewusst ohne Token und ausserhalb von `/api`.** Beides ist
        notwendig, nicht bequem. Jeder der fuenf `/api`-Router haengt am
        Waechter (siehe oben) - eine token-freie Ausnahme darunter waere
        eine Falle fuer die naechste Person, die einen sechsten Router
        ergaenzt und die Ausnahme nicht kennt. Und sie muss ueber HTTP
        erreichbar sein: man laedt sie genau dann, BEVOR man dem
        HTTPS-Zugang vertraut; ueber HTTPS abrufbar zu sein huelfe erst,
        wenn man sie schon nicht mehr braeuchte.

        Ausgeliefert wird der oeffentliche Teil der CA - kein Geheimnis.
        Die Schwaeche liegt woanders und steht so in der README: wer das
        installiert, vertraut dieser CA fuer JEDE Adresse, und der Transport
        hierher ist unverschluesselt. Wer im selben LAN dazwischenfunken
        kann, kann eine eigene CA unterschieben."""
        if tls_state is None or tls_state.material is None:
            raise HTTPException(
                status_code=503,
                detail="HTTPS ist fuer diesen Dienst nicht eingerichtet - es gibt kein "
                "Zertifikat zum Herunterladen.",
            )
        return FileResponse(
            tls_state.material.ca_certificate,
            media_type="application/x-x509-ca-cert",
            filename="loxmatter-ca.crt",
        )
```

- [ ] **Step 5: Tests laufen lassen**

Run: `uv run pytest tests/api/test_tls_routes.py -v`
Expected: PASS, alle 7 Tests.

- [ ] **Step 6: Die bestehende Suite gegenprüfen**

Run: `uv run pytest tests/api -q`
Expected: PASS. Besonders `tests/api/test_security.py` muss grün bleiben — es zählt die geschützten Router durch und darf durch den neuen Parameter nicht kippen.

- [ ] **Step 7: Linter und Typprüfung**

```bash
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src
```

Erwartet: keine Fehler.

- [ ] **Step 8: Committen**

```bash
git add src/loxmatter/api/diagnostics.py src/loxmatter/loxone/server.py tests/api/test_tls_routes.py
git commit -m "feat(api): CA-Zertifikat ausliefern und TLS-Zustand melden

/ca.crt liegt ausserhalb von /api und traegt kein Token: man laedt es
genau dann, wenn man dem HTTPS-Zugang noch nicht vertraut. Der
Zertifikatszustand bleibt unter /api/diagnostics/tls geschuetzt.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Zwei Listener in `cli.py`

**Files:**
- Modify: `src/loxmatter/cli.py` (Optionen in `run` ab Zeile 434, `_run`-Signatur ab Zeile 498, Serverstart ab Zeile 557)
- Create: `tests/test_cli_tls.py`

**Interfaces:**
- Consumes: `loxmatter.tls.prepare_tls`, `TlsState` (Task 1); `build_app(..., tls_state=...)` (Task 2).
- Produces:
  - `def _server_configs(app, host: str, listen: int, tls_state: TlsState) -> list[uvicorn.Config]`
  - `async def _serve_forever(servers: list[uvicorn.Server]) -> None`
  - `def _neutralize_signal_capture(server: uvicorn.Server) -> None`
  - Konstante `SHUTDOWN_TIMEOUT_SECONDS: Final = 10.0`
  - `_run(..., https_port: int = 8443, tls_dir: Path | None = None)`

- [ ] **Step 1: Den fehlschlagenden Test schreiben**

Create `tests/test_cli_tls.py`:

```python
"""Tests fuer den zweiten, TLS-gesicherten Listener (Entwurf 4.1/4.2).

Der wichtigste Test hier ist `test_cancelling_asks_every_server_to_stop`.
Er haelt einen am installierten uvicorn-Quelltext abgelesenen Fallstrick
fest: `Server.serve()` setzt seine Signal-Handler mit `signal.signal(...)`
(`uvicorn/server.py`, `capture_signals`), sodass bei zwei Servern im selben
Prozess der zweite den Handler des ersten ueberschreibt - ein Strg-C
beendete dann nur einen, der andere liefe weiter, und der Dienst liesse
sich nicht mehr beenden. Die Neutralisierung, die das aufloest, ist eine
einzige Zuweisung; ohne diesen Test kann sie bei einem uvicorn-Update
stillschweigend wirkungslos werden.

Die Server werden hier durch Doubles ersetzt, nicht wirklich gebunden: was
geprueft wird, ist die Abbruchmechanik, nicht dass uvicorn Sockets oeffnen
kann.
"""

from __future__ import annotations

import asyncio
import signal

import pytest
import uvicorn
from fastapi import FastAPI

from loxmatter.cli import _neutralize_signal_capture, _server_configs, _serve_forever
from loxmatter.tls import TlsState, prepare_tls


class FakeServer:
    """Verhaelt sich wie `uvicorn.Server`, soweit `_serve_forever` es sieht."""

    def __init__(self) -> None:
        self.should_exit = False
        self.started = False
        self.finished = False

    async def serve(self) -> None:
        self.started = True
        while not self.should_exit:
            await asyncio.sleep(0.01)
        self.finished = True


def test_without_tls_there_is_exactly_one_config():
    configs = _server_configs(
        FastAPI(), "0.0.0.0", 8080, TlsState(material=None, port=None, error=None)
    )

    assert len(configs) == 1
    assert configs[0].port == 8080
    assert configs[0].is_ssl is False


def test_with_tls_there_is_a_second_config_on_the_https_port(tmp_path):
    state = prepare_tls(tmp_path / "tls", 8443)

    configs = _server_configs(FastAPI(), "0.0.0.0", 8080, state)

    assert [config.port for config in configs] == [8080, 8443]
    assert configs[0].is_ssl is False
    assert configs[1].is_ssl is True
    assert configs[1].ssl_certfile == str(state.material.certificate)
    assert configs[1].ssl_keyfile == str(state.material.private_key)


def test_both_configs_serve_the_very_same_app(tmp_path):
    """Eine App, zwei Bindungen - kein zweiter Router, kein zweiter Zustand."""
    app = FastAPI()
    state = prepare_tls(tmp_path / "tls", 8443)

    configs = _server_configs(app, "0.0.0.0", 8080, state)

    assert configs[0].app is app
    assert configs[1].app is app


def test_a_neutralized_server_does_not_touch_the_signal_handlers():
    """Der Fallstrick aus Entwurf 4.2, an seiner Wurzel geprueft."""
    server = uvicorn.Server(uvicorn.Config(FastAPI()))
    before = signal.getsignal(signal.SIGINT)

    _neutralize_signal_capture(server)
    with server.capture_signals():
        during = signal.getsignal(signal.SIGINT)

    assert during is before
    assert signal.getsignal(signal.SIGINT) is before


async def test_cancelling_asks_every_server_to_stop():
    """Ohne das haenge der Dienst: der zweite Server wuerde beendet, der
    erste liefe weiter, und niemand saehe warum."""
    servers = [FakeServer(), FakeServer()]

    task = asyncio.create_task(_serve_forever(servers))  # type: ignore[arg-type]
    while not all(server.started for server in servers):
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert all(server.should_exit for server in servers)
    assert all(server.finished for server in servers)


async def test_a_single_server_still_works():
    servers = [FakeServer()]

    task = asyncio.create_task(_serve_forever(servers))  # type: ignore[arg-type]
    while not servers[0].started:
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert servers[0].finished
```

- [ ] **Step 2: Test laufen lassen und Fehlschlag bestätigen**

Run: `uv run pytest tests/test_cli_tls.py -v`
Expected: FAIL mit `ImportError: cannot import name '_neutralize_signal_capture' from 'loxmatter.cli'`.

- [ ] **Step 3: Die drei Bausteine in `cli.py` schreiben**

Imports in `src/loxmatter/cli.py` ergänzen — `contextlib` und `Iterator` zu den bestehenden Standardimports, `Final` zu `typing`, und nach `from loxmatter.model.store import Store`:

```python
from loxmatter.tls import TlsState, prepare_tls
```

Vor `_run` einfügen:

```python
# Wie lange nach einem Abbruch auf das geordnete Ende beider Server gewartet
# wird, bevor der Prozess ohnehin endet. Grosszuegig genug fuer offene
# WebSocket-Verbindungen (`/api/live`), kurz genug, dass ein zweites Strg-C
# nicht noetig wird.
SHUTDOWN_TIMEOUT_SECONDS: Final = 10.0


def _neutralize_signal_capture(server: uvicorn.Server) -> None:
    """Nimmt einem `uvicorn.Server` das Abfangen von SIGINT/SIGTERM.

    Der Grund steht in Entwurf 4.2 und ist am installierten Quelltext
    abgelesen: `Server.serve()` betritt `capture_signals()`, und das setzt
    seine Handler mit `signal.signal(sig, self.handle_exit)`. Zwei Server im
    selben Prozess bedeuten damit, dass der zweite den Handler des ersten
    UEBERSCHREIBT - bei einem Strg-C setzt nur der zweite sein
    `should_exit`, der erste bemerkt nichts und laeuft weiter. Der Dienst
    liesse sich nicht mehr beenden, mit einer Ursache, die in keinem Log
    steht.

    Ersetzt wird instanzweise, nicht auf der Klasse: ein anderer Aufrufer
    von `uvicorn` im selben Prozess (heute keiner, morgen vielleicht ein
    Test) soll davon unberuehrt bleiben.

    Ohne uvicorns Handler bleibt es bei dem Abbruchweg, den `_run`s
    Docstring ohnehin beschreibt - der SIGINT-Handler, den `asyncio.run`
    seit Python 3.11 selbst installiert, bricht den `_run`-Task ab.
    """

    @contextlib.contextmanager
    def _no_capture() -> Iterator[None]:
        yield

    server.capture_signals = _no_capture  # type: ignore[method-assign]


def _server_configs(
    app: FastAPI, host: str, listen: int, tls_state: TlsState
) -> list[uvicorn.Config]:
    """Eine Konfiguration fuer HTTP, bei eingerichtetem TLS eine zweite fuer
    HTTPS - beide auf DERSELBEN App (Entwurf 4.1).

    `/cmd` ist ueber HTTPS damit ebenfalls erreichbar und dort schlicht
    ungenutzt. Es dort zu sperren hiesse, dieselbe App an zwei Stellen
    unterschiedlich zusammenzubauen, fuer einen Gewinn von null."""
    configs = [uvicorn.Config(app, host=host, port=listen, log_level="info")]
    if tls_state.material is not None and tls_state.port is not None:
        configs.append(
            uvicorn.Config(
                app,
                host=host,
                port=tls_state.port,
                log_level="info",
                ssl_certfile=str(tls_state.material.certificate),
                ssl_keyfile=str(tls_state.material.private_key),
            )
        )
    return configs


async def _serve_forever(servers: list[uvicorn.Server]) -> None:
    """Laesst alle Server laufen und beendet bei einem Abbruch ALLE.

    `asyncio.shield` ist hier der Kern: ohne es wuerde der Abbruch die
    `serve()`-Tasks unmittelbar abbrechen, mitten in einer Anfrage, statt
    sie ueber `should_exit` geordnet auslaufen zu lassen. Mit ihm laufen die
    Tasks weiter, waehrend dieser Rahmen bereits die Abbruch-Ausnahme
    bekommt - dann wird `should_exit` gesetzt und auf ihr Ende gewartet.
    Der `raise` am Schluss reicht den Abbruch weiter an `_run`, dessen
    `finally`-Block Laufzeit, Sender und Client schliesst."""
    tasks = [asyncio.create_task(server.serve()) for server in servers]
    try:
        await asyncio.shield(asyncio.gather(*tasks))
    except asyncio.CancelledError:
        for server in servers:
            server.should_exit = True
        await asyncio.wait(tasks, timeout=SHUTDOWN_TIMEOUT_SECONDS)
        raise
```

`FastAPI` muss dafür importiert sein — falls `cli.py` es noch nicht importiert, ergänzen:

```python
from fastapi import FastAPI
```

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/test_cli_tls.py -v`
Expected: PASS, alle 6 Tests.

- [ ] **Step 5: Die Bausteine in `run`/`_run` verdrahten**

In `run`, nach der `matter_data_dir`-Option (endet Zeile 466), zwei Optionen ergänzen:

```python
    https_port: int = typer.Option(
        8443,
        "--https-port",
        help="Port fuer den zusaetzlichen HTTPS-Listener der WebUI. `0` schaltet "
        "ihn ab. Er liefert dieselbe App wie --listen; `/cmd` und `/resync` "
        "bleiben davon unberuehrt und weiter ueber HTTP erreichbar, weil der "
        "Miniserver ein selbstsigniertes Zertifikat nicht akzeptiert. HTTPS "
        "wird gebraucht, damit der Browser die Kamera fuer den QR-Scan "
        "freigibt - ueber eine LAN-Adresse ohne TLS bleibt sie gesperrt.",
    ),
    tls_dir: Path | None = typer.Option(  # noqa: B008
        None,
        "--tls-dir",
        help="Wo Zertifikat und Schluessel liegen. Ohne Angabe ein "
        "Unterverzeichnis `tls` neben der Datenbank. Wird beim Start "
        "angelegt und befuellt; schlaegt das fehl, startet der Dienst "
        "trotzdem - nur ohne HTTPS und mit einer Warnung im Log.",
    ),
```

Am Ende von `run`, die letzte Zeile (Zeile 495) ersetzen:

```python
    asyncio.run(
        _run(
            store,
            url,
            miniserver,
            port,
            listen,
            matter_data_dir,
            host,
            api_token,
            https_port,
            tls_dir if tls_dir is not None else resolved_store_path.parent / "tls",
        )
    )
```

`_run`-Signatur (Zeile 498-507) um zwei Parameter erweitern:

```python
async def _run(
    store: Store,
    url: str,
    miniserver: str,
    port: int,
    listen: int,
    matter_data_dir: Path | None = None,
    host: str = "0.0.0.0",  # Standard wie in `run` — der Miniserver muss den Dienst erreichen
    api_token: str | None = None,
    https_port: int = 8443,
    tls_dir: Path | None = None,
) -> None:
```

Und den Serverstart (Zeilen 557-575) ersetzen:

```python
        # TLS zuerst, dann die App: `build_app` braucht den Zustand, um
        # `/ca.crt` auszuliefern und `/api/diagnostics/tls` zu beantworten.
        # `prepare_tls` wirft nie (siehe dort) - schlaegt die Erzeugung fehl,
        # laeuft der Dienst wie bisher nur ueber HTTP.
        tls_state = prepare_tls(
            tls_dir if tls_dir is not None else Path("tls"), https_port
        )
        app = build_app(
            store,
            invoke,
            runtime,
            client=client,
            sender=sender,
            matter_data_dir=matter_data_dir,
            api_token=api_token,
            tls_state=tls_state,
        )
        servers = [uvicorn.Server(config) for config in _server_configs(app, host, listen, tls_state)]
        for server in servers:
            _neutralize_signal_capture(server)
        await _serve_forever(servers)
```

- [ ] **Step 6: Von Hand starten und beide Listener prüfen**

```bash
uv run loxmatter run --miniserver 127.0.0.1 --url ws://127.0.0.1:9 --tls-dir /tmp/loxmatter-tls
```

Erwartet: Abbruch mit „matter-server unter ws://127.0.0.1:9 nicht erreichbar" — das belegt, dass die neuen Optionen die Argumentprüfung passieren. Anschließend gegen einen laufenden matter-server starten und prüfen:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/health && curl -sk -o /dev/null -w "%{http_code}\n" https://127.0.0.1:8443/health
```

Erwartet: zweimal `200`. Danach Strg-C — der Prozess muss **beim ersten Mal** enden, nicht erst beim zweiten.

- [ ] **Step 7: Gesamte Suite, Linter, Typprüfung**

```bash
uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src
```

Erwartet: keine Fehler.

- [ ] **Step 8: Committen**

```bash
git add src/loxmatter/cli.py tests/test_cli_tls.py
git commit -m "feat(cli): zweiten Listener fuer HTTPS starten

Beide Server liefern dieselbe App. uvicorn setzt seine Signal-Handler je
serve()-Aufruf neu, weshalb der zweite Server den ersten sonst taub
machte und sich der Dienst nicht mehr beenden liesse - capture_signals
wird deshalb instanzweise neutralisiert und der Abbruch selbst geregelt.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: jsQR vendoren und der Bildweg

**Files:**
- Create: `src/loxmatter/web/vendor/jsqr.js`
- Modify: `src/loxmatter/web/index.html` (Kopfkommentar ab Zeile 2, Einlern-Kasten Zeilen 99-127, Skript-Einbindung am Dateiende)
- Modify: `src/loxmatter/web/app.js` (Konstanten am Dateikopf, Zustand nach Zeile 242, Methoden nach `commissionDevice`)
- Modify: `src/loxmatter/web/style.css`
- Modify: `tests/api/test_web.py`

**Interfaces:**
- Consumes: nichts aus früheren Tasks (reines Frontend gegen die bestehende Route `POST /api/devices/commission`).
- Produces (in `app.js`, global im Datei-Scope):
  - `const SCAN_INTERVAL_MS = 250`
  - `const MATTER_CODE_PATTERN`
  - `function looksLikeMatterCode(text) -> boolean`
  - `async function decodeQrFromBlob(blob) -> string | null`
  - Auf dem `app()`-Objekt: `qrMessage`, `qrMessageIsError`, `setQrMessage(text, isError)`, `handleScannedImage(file)`, `acceptScannedCode(text)`, `onQrPaste(event)`, `onQrDrop(event)`

- [ ] **Step 1: Die Bibliothek vendoren**

```bash
curl -sSL https://cdn.jsdelivr.net/npm/jsqr@1.4.0/dist/jsQR.js -o src/loxmatter/web/vendor/jsqr.js && shasum -a 256 src/loxmatter/web/vendor/jsqr.js
```

Erwartet: `bc40c8a15196236b2314db0856f72ca0b49980cd5413b8c852a7349f5fee0859`. **Stimmt die Prüfsumme nicht, hier abbrechen** und nachfragen — eine ungeprüfte Fremddatei wird nicht eingecheckt.

- [ ] **Step 2: Die fehlschlagenden Tests schreiben**

In `tests/api/test_web.py` ans Dateiende anhängen:

```python
# ---------------------------------------------------------------------------
# QR-Einlernen (Entwurf 2026-09-03, Abschnitte 3 und 7). Ohne Browser laesst
# sich hier nichts klicken - pruefbar ist, dass die ausgelieferten Dateien
# die Eigenschaften tragen, ohne die die Bedienung nachweislich nicht
# funktionieren KANN.
# ---------------------------------------------------------------------------


async def test_the_commission_card_offers_a_qr_scan(api):
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    assert "QR-Code scannen" in page


async def test_the_image_route_works_without_any_certificate(api):
    """Der Rueckfallweg aus Entwurf 3: ein Dateifeld, das auf dem Handy die
    Kamera-App oeffnet. Es braucht keinen secure context und muss deshalb
    IMMER im Markup stehen, nicht hinter einer Bedingung."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    assert 'capture="environment"' in page
    assert 'accept="image/*"' in page


async def test_jsqr_is_served_locally_not_from_a_cdn(api):
    """Dieselbe Zusage wie bei Alpine: die Bruecke laeuft ohne Internet."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert "cdn." not in page
    assert "unpkg" not in page
    assert "/static/vendor/jsqr.js" in page
    assert (await client.get("/static/vendor/jsqr.js")).status_code == 200


async def test_the_image_never_leaves_the_browser(api):
    """Entwurf 7: dekodiert wird im Browser. Gaebe es eine Route, an die ein
    Foto ginge, laege es ueber HTTP im Klartext auf dem Kabel."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert "jsQR(" in script
    assert "/api/devices/decode" not in script


async def test_only_something_that_looks_like_a_matter_code_starts_commissioning(api):
    """Entwurf 3: sonst startete ein zufaellig ins Bild geratener WLAN- oder
    Paket-QR einen sinnlosen Einlernversuch, mit einer Fehlermeldung, die
    nach einem Geraetedefekt klaenge."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert "looksLikeMatterCode" in script
    assert "MT:" in script
```

- [ ] **Step 3: Tests laufen lassen und Fehlschlag bestätigen**

Run: `uv run pytest tests/api/test_web.py -k "qr or matter_code or image" -v`
Expected: FAIL — `test_the_commission_card_offers_a_qr_scan` mit `assert 'QR-Code scannen' in page`.

- [ ] **Step 4: Den Dekodierpfad in `app.js` ergänzen**

Am Dateikopf von `src/loxmatter/web/app.js`, nach `const TOKEN_STORAGE_KEY = "loxmatter_token";` einfügen:

```js
// Wie oft der Sucher ein Bild durch jsQR schickt (Task 5). 250 ms sind
// deutlich schneller, als jemand einen Code ruhig vor die Kamera haelt, und
// lassen dem Hauptthread genug Luft - jeder Durchlauf rechnet ein volles
// `ImageData` durch.
const SCAN_INTERVAL_MS = 250;

// Was als Matter-Pairing-Code durchgeht (Entwurf Abschnitt 3): elf Ziffern
// oder der `MT:`-Code. Bewusst KEINE Zerlegung des Payloads - das
// Discovery-Bitfeld darin beschreibt die Wege zur Inbetriebnahme, nicht das
// spaetere Funknetz, und taugt deshalb nicht fuer den naheliegenden Hinweis
// "Thread-Geraet erkannt" (Entwurf 3.1).
const MATTER_CODE_PATTERN = /^(?:MT:[0-9A-Z.$%*+\-./:]{10,}|\d{11})$/;

function looksLikeMatterCode(text) {
  return MATTER_CODE_PATTERN.test(text.trim());
}

/**
 * Dekodiert einen QR-Code aus einem Bild - Foto, Datei oder Zwischenablage.
 *
 * Alles im Browser, nichts ueber die Leitung (Entwurf 7): das Bild geht auf
 * ein `<canvas>`, dessen `ImageData` an jsQR, heraus kommt der String. Ueber
 * eine unverschluesselte HTTP-Verbindung ist das kein Nebenaspekt - ein
 * hochgeladenes Foto laege im Klartext auf dem Kabel.
 *
 * Gibt `null` zurueck, wenn im Bild kein Code steckt. Wirft, wenn das Bild
 * selbst nicht lesbar ist (kaputte Datei, nicht unterstuetztes Format).
 */
async function decodeQrFromBlob(blob) {
  const bitmap = await createImageBitmap(blob);
  const canvas = document.createElement("canvas");
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  context.drawImage(bitmap, 0, 0);
  bitmap.close();
  const image = context.getImageData(0, 0, canvas.width, canvas.height);
  const found = jsQR(image.data, image.width, image.height);
  return found ? found.data : null;
}
```

Im `app()`-Objekt, nach `commissionMessageIsError: false,` (Zeile 242) einfügen:

```js
    // Einlernen per QR-Code (Entwurf 2026-09-03). `qrMessage` ist von
    // `commissionMessage` getrennt, weil ein Scanfehler ("kein Code im
    // Bild") etwas anderes ist als ein Einlernfehler ("Geraet hat
    // abgelehnt") - und beide gleichzeitig sichtbar sein koennen.
    qrMessage: null,
    qrMessageIsError: false,
```

Nach der Methode `commissionDevice()` (endet Zeile 679) einfügen:

```js
    setQrMessage(text, isError) {
      this.qrMessage = text;
      this.qrMessageIsError = isError;
    },

    /** Ein Bild vom Dateifeld, aus der Zwischenablage oder per Drag & Drop. */
    async handleScannedImage(file) {
      if (!file) {
        return;
      }
      this.setQrMessage(null, false);
      let text;
      try {
        text = await decodeQrFromBlob(file);
      } catch (error) {
        this.setQrMessage(`Das Bild konnte nicht gelesen werden: ${error.message}`, true);
        return;
      }
      if (text === null) {
        this.setQrMessage(
          "Kein QR-Code im Bild erkannt. Näher heran, mehr Licht, und den ganzen Code ins Bild.",
          true,
        );
        return;
      }
      this.acceptScannedCode(text);
    },

    /**
     * Nimmt einen dekodierten String an - und lernt sofort ein (Entwurf 3).
     *
     * Der Code bleibt dabei SICHTBAR im Feld stehen. Scheitert das Einlernen
     * (der wahrscheinlichste Fall: ein Thread-Geraet ohne Datensatz), traegt
     * man den Datensatz nach und drueckt „Einlernen" - kein zweiter Scan.
     */
    acceptScannedCode(text) {
      const code = text.trim();
      if (!looksLikeMatterCode(code)) {
        this.setQrMessage(
          "Das ist kein Matter-Pairing-Code – erwartet werden elf Ziffern oder ein Code, " +
            "der mit „MT:“ beginnt.",
          true,
        );
        return;
      }
      this.commissionCode = code;
      this.setQrMessage(null, false);
      this.commissionDevice();
    },

    /**
     * Strg+V. `event.clipboardData.files` braucht KEINEN secure context -
     * anders als `navigator.clipboard.read()`, das hier deshalb nicht
     * verwendet wird und ueber HTTP gar nicht ginge.
     *
     * Der Griff nach `tagName` ist kein Beiwerk: der Handler haengt am
     * Fenster, und ohne ihn schluckte ein Einfuegen ins Token- oder
     * Pairing-Feld den Text, statt ihn dort landen zu lassen.
     */
    onQrPaste(event) {
      const target = event.target;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA")) {
        return;
      }
      const files = event.clipboardData ? event.clipboardData.files : null;
      if (!files || files.length === 0) {
        return;
      }
      event.preventDefault();
      this.handleScannedImage(files[0]);
    },

    onQrDrop(event) {
      const files = event.dataTransfer ? event.dataTransfer.files : null;
      if (!files || files.length === 0) {
        return;
      }
      this.handleScannedImage(files[0]);
    },
```

- [ ] **Step 5: Das Markup ergänzen**

Im Kopfkommentar von `src/loxmatter/web/index.html`, nach dem Alpine-Absatz (endet Zeile 12), einfügen. **`cdn.` darf hier nicht vorkommen** — jsDelivr wird beim Namen genannt, nicht als Hostname (`tests/api/test_web.py`):

```
  Enthaelt ausserdem eine vendorte Kopie von jsQR, Version 1.4.0 (MIT-Lizenz,
  (c) Cosmo Wolfe), bezogen als UMD-Distribution des npm-Pakets "jsqr" ueber
  jsDelivr (das oeffentliche Auslieferungsnetz fuer npm-Pakete) am
  2026-09-03, unveraendert unter web/vendor/jsqr.js abgelegt - SHA-256
  bc40c8a15196236b2314db0856f72ca0b49980cd5413b8c852a7349f5fee0859. Sie wird
  nur unminifiziert ausgeliefert (251 KB); das ist bewusst hingenommen, weil
  sie lokal liegt und einmalig laedt - ein Nachladen erst bei Bedarf waere
  eine zweite Ladestrecke samt Fehlerbehandlung fuer einen Gewinn, den man
  auf einem LAN nicht misst.
```

Den Einlern-Kasten (Zeilen 99-127) ersetzen durch:

```html
        <div class="card">
          <h2>Neues Gerät einlernen</h2>
          <div class="row">
            <input
              type="text"
              x-model="commissionCode"
              placeholder="Pairing-Code (11-stellig oder MT:…)"
            />
            <input
              type="text"
              x-model="commissionThreadDataset"
              placeholder="Thread-Datensatz (nur bei Thread-Geräten)"
            />
            <button class="primary" @click="commissionDevice()" :disabled="commissionBusy">
              Einlernen
            </button>
          </div>

          <!--
            Der Bildweg steht hier ohne jede Bedingung im Markup: er braucht
            keinen secure context und ist damit der einzige Weg, der auch
            dann noch traegt, wenn das Zertifikat auf einem Geraet zickt
            (Entwurf Abschnitt 3). `capture="environment"` oeffnet auf dem
            Handy die Kamera-App, auf dem Rechner einen Dateiwaehler.
            Das Zuruecksetzen von `value` nach dem Auslesen ist noetig, damit
            dasselbe Bild ein zweites Mal ausgewaehlt werden kann - ohne das
            feuert `change` beim zweiten Mal nicht.
          -->
          <div
            class="qr-drop"
            @drop.prevent="onQrDrop($event)"
            @dragover.prevent
            @paste.window="onQrPaste($event)"
          >
            <label class="qr-file">
              <input
                type="file"
                accept="image/*"
                capture="environment"
                @change="handleScannedImage($event.target.files[0]); $event.target.value = ''"
              />
              <span>QR-Code fotografieren oder Bild wählen</span>
            </label>
            <span class="hint">
              …oder ein Bild hierher ziehen bzw. mit Strg+V einfügen.
            </span>
          </div>

          <p
            x-show="qrMessage"
            x-cloak
            :class="qrMessageIsError ? 'banner danger' : 'banner ok'"
            x-text="qrMessage"
          ></p>

          <p class="hint">
            Hängt das Gerät schon in Apple, Google oder einer DIRIGERA, funktioniert der
            aufgedruckte Code hier nicht mehr – dort einen zusätzlichen Multi-Admin-Code erzeugen
            und stattdessen diesen eingeben.
          </p>
          <p
            x-show="commissionMessage"
            x-cloak
            :class="commissionMessageIsError ? 'banner danger' : 'banner ok'"
            x-text="commissionMessage"
          ></p>
        </div>
```

Die Skript-Einbindung am Dateiende: **jsQR muss vor `app.js` geladen werden**, weil `decodeQrFromBlob` das globale `jsQR` benutzt. Die vorhandene Zeile für `alpine.min.js` suchen und `jsqr.js` davor sowie vor `app.js` einreihen:

```html
    <script src="/static/vendor/jsqr.js"></script>
    <script src="/static/app.js"></script>
    <script defer src="/static/vendor/alpine.min.js"></script>
```

- [ ] **Step 6: Das Stylesheet ergänzen**

An `src/loxmatter/web/style.css` anhängen:

```css
/* Ablagefeld fuer den Bildweg des QR-Scans (Entwurf Abschnitt 3). */
.qr-drop {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem;
  margin-top: 0.75rem;
  padding: 0.75rem;
  border: 1px dashed var(--border, #ccc);
  border-radius: 6px;
}

.qr-file {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  cursor: pointer;
}
```

- [ ] **Step 7: Tests laufen lassen**

Run: `uv run pytest tests/api/test_web.py -v`
Expected: PASS, alle Tests — die fünf neuen und die 16 bestehenden. Schlägt `test_alpine_is_served_locally_not_from_a_cdn` fehl, steht `cdn.` im Kopfkommentar; dort den Hostnamen entfernen.

- [ ] **Step 8: Committen**

```bash
git add src/loxmatter/web/vendor/jsqr.js src/loxmatter/web/index.html src/loxmatter/web/app.js src/loxmatter/web/style.css tests/api/test_web.py
git commit -m "feat(web): QR-Code aus einem Bild einlesen

Foto per Kamera-App, Datei, Drag and Drop oder Strg+V - alles ueber
denselben Dekodierpfad, vollstaendig im Browser. Das Bild verlaesst ihn
nie; ueber HTTP laege ein hochgeladenes Foto im Klartext auf dem Kabel.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Der Sucher und der Wechsel auf HTTPS

**Files:**
- Modify: `src/loxmatter/web/app.js` (Zustand und Methoden aus Task 4 erweitern)
- Modify: `src/loxmatter/web/index.html` (Einlern-Kasten aus Task 4 erweitern)
- Modify: `src/loxmatter/web/style.css`
- Modify: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `looksLikeMatterCode`, `SCAN_INTERVAL_MS`, `setQrMessage`, `acceptScannedCode` (Task 4); `GET /api/diagnostics/tls` mit den Feldern `enabled`, `port`, `error` (Task 2).
- Produces: auf dem `app()`-Objekt `qrOpen`, `qrSwitchOpen`, `scannerActive`, `scanTimer`, `stream`, `tls`, `tlsError`, `canUseCamera()`, `canOfferQrScan()`, `startQrScan()`, `openScanner()`, `scanFrame()`, `closeScanner()`, `switchToHttps()`, `loadTls()`.

- [ ] **Step 1: Die fehlschlagenden Tests schreiben**

In `tests/api/test_web.py` ans Dateiende anhängen:

```python
async def test_the_camera_decision_is_made_by_secure_context_not_by_scheme(api):
    """Sonst behauptete die Oberflaeche auf einem `http://localhost`-Aufruf
    faelschlich, die Kamera sei gesperrt, obwohl sie dort erlaubt ist
    (Entwurf Abschnitt 3)."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert "isSecureContext" in script


async def test_the_scanner_stops_itself_on_the_first_hit(api):
    """Entwurf 3: sonst startete ein zweites Erkennen desselben Codes eine
    zweite Einlernanfrage, waehrend die erste noch laeuft."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert "closeScanner" in script
    assert "getTracks" in script


async def test_a_browser_without_a_camera_api_is_not_offered_a_scanner(api):
    """Entwurf Abschnitt 8: ohne Kamera erscheint der Knopf gar nicht erst -
    ein Knopf, der nur eine Fehlermeldung erzeugen kann, ist schlechter als
    keiner. Ueber HTTP bleibt er trotzdem sichtbar, denn dort bietet er den
    Wechsel an, nicht den Sucher."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    script = (await client.get("/static/app.js")).text
    assert "canOfferQrScan()" in page
    assert "canOfferQrScan()" in script


async def test_the_switch_warns_about_the_token_before_it_is_gone(api):
    """`localStorage` ist origin-gebunden - nach dem Sprung ist das Token
    weg. Ohne Ansage sieht das aus wie ein Defekt (Entwurf Abschnitt 6)."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    assert "Token" in page
    assert "Zertifikat" in page


async def test_the_switch_does_not_carry_the_token_in_the_url(api):
    """Auch nicht als Fragment: das stuende in der Browser-History und in
    jedem geteilten Link (Entwurf Abschnitt 6)."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert "#token=" not in script
    assert "location.hash" not in script
    # Der Wechsel baut die Zieladresse aus Host und Port - mehr nicht.
    assert "window.location.href = `https://${window.location.hostname}:" in script
```

- [ ] **Step 2: Tests laufen lassen und Fehlschlag bestätigen**

Run: `uv run pytest tests/api/test_web.py -k "secure_context or scanner_stops or switch" -v`
Expected: FAIL — `assert 'isSecureContext' in script`.

- [ ] **Step 3: Zustand und Methoden in `app.js` ergänzen**

Im `app()`-Objekt, direkt nach `qrMessageIsError: false,` (aus Task 4) einfügen:

```js
    qrOpen: false,
    qrSwitchOpen: false,
    scannerActive: false,
    scanTimer: null,
    stream: null,

    // Zustand des HTTPS-Listeners, gelesen aus `/api/diagnostics/tls`.
    // Wird nur bei Bedarf geladen (siehe `startQrScan`) - wer nie scannt,
    // braucht die Antwort nicht.
    tls: null,
    tlsError: null,
```

Nach `onQrDrop(event)` (aus Task 4) einfügen:

```js
    /**
     * Ob dieser Browser die Kamera ueberhaupt freigeben darf.
     *
     * Entscheidend ist `isSecureContext`, NICHT das Schema in der
     * Adresszeile: ueber `http://localhost` ist die Kamera erlaubt, ueber
     * `http://192.168.x.x` nicht. Eine Weiche am Schema behauptete im
     * ersten Fall faelschlich, sie sei gesperrt.
     */
    canUseCamera() {
      return window.isSecureContext && !!navigator.mediaDevices;
    },

    /**
     * Ob der Knopf „QR-Code scannen" ueberhaupt angeboten wird.
     *
     * Ohne secure context ja - dort bietet er nicht den Sucher an, sondern
     * den Wechsel, und der ist genau dann sinnvoll. MIT secure context nur,
     * wenn es die Kamera-API ueberhaupt gibt: ein Knopf, der nichts als
     * eine Fehlermeldung erzeugen kann, ist schlechter als keiner (Entwurf
     * Abschnitt 8). Der Bildweg darunter bleibt in beiden Faellen sichtbar.
     */
    canOfferQrScan() {
      return !window.isSecureContext || !!navigator.mediaDevices;
    },

    /** Der Knopf „QR-Code scannen": Sucher, oder die Erklärung zum Wechsel. */
    async startQrScan() {
      if (this.canUseCamera()) {
        await this.openScanner();
        return;
      }
      await this.loadTls();
      this.qrSwitchOpen = true;
    },

    async loadTls() {
      this.tlsError = null;
      try {
        this.tls = await this.request("GET", "/api/diagnostics/tls");
      } catch (error) {
        this.tls = null;
        this.tlsError = `Der Zertifikatszustand ist nicht abrufbar: ${error.message}`;
      }
    },

    async openScanner() {
      this.qrSwitchOpen = false;
      this.setQrMessage(null, false);
      try {
        this.stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: "environment" } },
          audio: false,
        });
      } catch (error) {
        this.setQrMessage(
          "Die Kamera wurde nicht freigegeben. Der Weg über ein Foto darunter " +
            "funktioniert trotzdem.",
          true,
        );
        return;
      }
      this.qrOpen = true;
      this.scannerActive = true;
      await this.$nextTick();
      const video = this.$refs.qrVideo;
      video.srcObject = this.stream;
      await video.play();
      this.scanFrame();
    },

    /**
     * Ein Bild pro Durchlauf durch jsQR, danach neu einplanen.
     *
     * Ein erkannter Code, der KEIN Matter-Code ist, beendet den Sucher
     * nicht - er sagt es nur und scannt weiter. Sonst muesste man den
     * Sucher nach jedem versehentlich erfassten Plakat-QR neu oeffnen.
     */
    scanFrame() {
      if (!this.scannerActive) {
        return;
      }
      const video = this.$refs.qrVideo;
      if (video && video.readyState >= 2 && video.videoWidth > 0) {
        const canvas = this.$refs.qrCanvas;
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        const context = canvas.getContext("2d", { willReadFrequently: true });
        context.drawImage(video, 0, 0, canvas.width, canvas.height);
        const image = context.getImageData(0, 0, canvas.width, canvas.height);
        const found = jsQR(image.data, image.width, image.height);
        if (found && looksLikeMatterCode(found.data)) {
          const code = found.data;
          this.closeScanner();
          this.acceptScannedCode(code);
          return;
        }
        if (found) {
          this.setQrMessage(
            "Erkannt, aber das ist kein Matter-Pairing-Code – erwartet werden elf " +
              "Ziffern oder ein Code, der mit „MT:“ beginnt.",
            true,
          );
        }
      }
      this.scanTimer = window.setTimeout(() => this.scanFrame(), SCAN_INTERVAL_MS);
    },

    /**
     * Beendet Schleife UND Kamera.
     *
     * Ohne `getTracks().forEach(stop)` bliebe die Kameraleuchte an, nachdem
     * der Sucher verschwunden ist - fuer eine Person am Geraet sieht das
     * aus, als filme die Seite weiter.
     */
    closeScanner() {
      this.scannerActive = false;
      this.qrOpen = false;
      if (this.scanTimer !== null) {
        window.clearTimeout(this.scanTimer);
        this.scanTimer = null;
      }
      if (this.stream !== null) {
        this.stream.getTracks().forEach((track) => track.stop());
        this.stream = null;
      }
    },

    /**
     * Wechselt auf denselben Host über HTTPS.
     *
     * Das Token bleibt bewusst zurueck: `localStorage` ist origin-gebunden,
     * und es mitzunehmen ginge nur ueber die URL - als Query-Parameter
     * landete es in Server- und Proxy-Logs, als Fragment in der
     * Browser-History und in jedem geteilten Link. Es wird drueben einmal
     * neu eingetragen; die Erklärung darüber sagt das vorher an.
     */
    switchToHttps() {
      if (!this.tls || !this.tls.enabled || !this.tls.port) {
        return;
      }
      window.location.href = `https://${window.location.hostname}:${this.tls.port}/`;
    },
```

- [ ] **Step 4: Das Markup ergänzen**

Im Einlern-Kasten, direkt nach der schließenden `</div>` der ersten `<div class="row">` (also vor `<div class="qr-drop">` aus Task 4), einfügen:

```html
          <div class="row">
            <button @click="startQrScan()" x-show="!qrOpen && canOfferQrScan()">
              QR-Code scannen
            </button>
            <button class="danger" @click="closeScanner()" x-show="qrOpen" x-cloak>
              Sucher schließen
            </button>
          </div>

          <!--
            Der Sucher. `x-ref` statt einer Id, weil Alpine die Elemente
            ueber `$refs` findet, ohne dass ein zweites Vorkommen derselben
            Id auf der Seite je zum Problem werden koennte. Das Canvas ist
            verborgen - es dient nur als Zwischenablage fuer jsQR, nicht als
            Anzeige.
          -->
          <div class="qr-scanner" x-show="qrOpen" x-cloak>
            <video x-ref="qrVideo" playsinline muted></video>
            <canvas x-ref="qrCanvas" hidden></canvas>
          </div>

          <!--
            Ohne secure context gibt der Browser die Kamera nicht frei -
            ueber eine LAN-Adresse ohne HTTPS also nie. Statt eines Suchers,
            der sich nicht oeffnen laesst, steht hier die Erklaerung samt
            Wechsel. Beide Ueberraschungen, die danach kommen, werden VORHER
            genannt: die Zertifikatswarnung und das fehlende Token.
          -->
          <div class="banner warn" x-show="qrSwitchOpen" x-cloak>
            <p>
              Die Kamera gibt der Browser nur über eine verschlüsselte Verbindung frei. Diese
              Brücke bringt dafür ein selbst erzeugtes Zertifikat mit.
            </p>
            <p>
              <strong>Was gleich passiert:</strong> der Browser warnt vor dem Zertifikat – das ist
              hier erwartet und kein Angriff, die Warnung lässt sich wegklicken. Danach ist das
              API-Token weg und muss einmal neu eingetragen werden, weil es je Adresse getrennt
              gespeichert wird. Bleiben Sie danach am besten auf der verschlüsselten Adresse: dort
              geht das Token nicht mehr im Klartext über das Netz.
            </p>
            <p x-show="tlsError" x-cloak class="banner danger" x-text="tlsError"></p>
            <p x-show="tls && !tls.enabled && tls.error" x-cloak class="banner danger">
              Für diesen Dienst ist kein Zertifikat eingerichtet:
              <span x-text="tls ? tls.error : ''"></span> Der Weg über ein Foto darunter
              funktioniert trotzdem.
            </p>
            <p x-show="tls && !tls.enabled && !tls.error" x-cloak class="hint">
              Der verschlüsselte Zugang ist abgeschaltet (<code>--https-port 0</code>). Der Weg
              über ein Foto darunter funktioniert trotzdem.
            </p>
            <div class="row">
              <button class="primary" x-show="tls && tls.enabled" @click="switchToHttps()">
                Zur sicheren Verbindung wechseln
              </button>
              <button @click="qrSwitchOpen = false">Abbrechen</button>
            </div>
          </div>
```

- [ ] **Step 5: Das Stylesheet ergänzen**

An `src/loxmatter/web/style.css` anhängen:

```css
/* Sucher fuer den QR-Scan (Entwurf Abschnitt 3). Begrenzte Breite, damit
   das Kamerabild auf einem Rechner nicht die halbe Seite einnimmt. */
.qr-scanner {
  margin-top: 0.75rem;
}

.qr-scanner video {
  width: 100%;
  max-width: 24rem;
  border-radius: 6px;
  background: #000;
}
```

- [ ] **Step 6: Tests laufen lassen**

Run: `uv run pytest tests/api/test_web.py -v`
Expected: PASS, alle Tests.

- [ ] **Step 7: Committen**

```bash
git add src/loxmatter/web/index.html src/loxmatter/web/app.js src/loxmatter/web/style.css tests/api/test_web.py
git commit -m "feat(web): Sucher fuer den QR-Scan samt Wechsel auf HTTPS

Die Weiche ist isSecureContext, nicht das Schema - ueber
http://localhost ist die Kamera erlaubt, ueber eine LAN-Adresse nicht.
Der Wechsel sagt beide Ueberraschungen vorher an: die
Zertifikatswarnung und das Token, das origin-gebunden zurueckbleibt.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Zertifikatsabschnitt in Ansicht 4 („System")

**Files:**
- Modify: `src/loxmatter/web/index.html` (System-Ansicht ab Zeile 450)
- Modify: `src/loxmatter/web/app.js` (`loadSystem` erweitern)
- Modify: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `loadTls()`, `tls`, `tlsError` (Task 5); `GET /ca.crt` (Task 2).
- Produces: nichts, was ein späterer Task benutzt.

- [ ] **Step 1: Die fehlschlagenden Tests schreiben**

In `tests/api/test_web.py` ans Dateiende anhängen:

```python
async def test_the_system_view_offers_the_ca_for_download(api):
    """Ein gewoehnlicher Link ist hier richtig: `/ca.crt` liegt ausserhalb
    von `/api` und traegt kein Token (siehe `test_no_plain_link_points_at_a_
    token_protected_route`, das genau deshalb nur `/api` verbietet)."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    assert 'href="/ca.crt"' in page


async def test_the_system_view_names_the_step_everyone_forgets_on_ios(api):
    """Ohne „Zertifikatsvertrauen" bleibt ein installiertes Profil auf iOS
    wirkungslos - und niemand sieht, woran es liegt (Entwurf Abschnitt 5)."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    assert "Zertifikatsvertrauen" in page


async def test_the_system_view_is_honest_about_what_installing_the_ca_means(api):
    """Wer sie installiert, vertraut ihr fuer JEDE Adresse. Das steht in der
    README und muss auch dort stehen, wo geklickt wird."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    assert "jede Adresse" in page
```

- [ ] **Step 2: Tests laufen lassen und Fehlschlag bestätigen**

Run: `uv run pytest tests/api/test_web.py -k "system_view" -v`
Expected: FAIL — `assert 'href="/ca.crt"' in page`.

- [ ] **Step 3: `loadSystem` erweitern**

In `src/loxmatter/web/app.js` die Methode `loadSystem()` suchen und am Ende ihres Körpers ergänzen:

```js
      await this.loadTls();
```

- [ ] **Step 4: Das Markup ergänzen**

In `src/loxmatter/web/index.html`, in der System-Ansicht nach dem `Systemcheck`-Kasten (schließende `</div>` bei Zeile 466), einfügen:

```html
        <div class="card">
          <h2>Verschlüsselter Zugang</h2>

          <p x-show="tlsError" x-cloak class="banner danger" x-text="tlsError"></p>

          <template x-if="tls && tls.enabled">
            <div>
              <p>
                Aktiv auf Port <strong x-text="tls.port"></strong>, gültig bis
                <strong x-text="tls.expires ? tls.expires.slice(0, 10) : ''"></strong>. Gilt für:
                <span x-text="['localhost', '127.0.0.1', ...tls.addresses].join(', ')"></span>.
              </p>
              <p class="hint">
                Läuft das Zertifikat ab oder bekommt die Brücke eine andere IP-Adresse, erneuert
                sie es beim nächsten Start von selbst. Die Zertifizierungsstelle unten bleibt
                dabei dieselbe – einmal eingerichtetes Vertrauen auf einem Gerät hält.
              </p>
              <p>
                <a href="/ca.crt" download>Zertifikat der Brücke herunterladen</a>
              </p>
              <p class="banner warn">
                <strong>Was das bedeutet:</strong> ein Gerät, das dieser Zertifizierungsstelle
                vertraut, vertraut ihr für jede Adresse – nicht nur für diese Brücke. Die Datei
                kommt außerdem unverschlüsselt zu Ihnen. Wer im selben Netz mitschneiden kann,
                könnte an dieser Stelle eine eigene unterschieben. Installieren Sie sie nur in
                einem Netz, dem Sie vertrauen.
              </p>
              <h3>iPhone und iPad</h3>
              <ol class="hint">
                <li>Datei laden – iOS legt sie als Profil ab.</li>
                <li>Einstellungen → Allgemein → VPN und Geräteverwaltung → Profil installieren.</li>
                <li>
                  Einstellungen → Allgemein → Info → <strong>Zertifikatsvertrauen</strong> → den
                  Eintrag „loxmatter local CA" einschalten. Ohne diesen dritten Schritt bleibt das
                  Profil wirkungslos.
                </li>
              </ol>
              <h3>Android</h3>
              <ol class="hint">
                <li>Datei laden.</li>
                <li>Einstellungen → Sicherheit → Verschlüsselung und Anmeldedaten.</li>
                <li>Zertifikat installieren → CA-Zertifikat → die geladene Datei wählen.</li>
              </ol>
            </div>
          </template>

          <template x-if="tls && !tls.enabled">
            <div>
              <p x-show="tls.error" x-cloak class="banner danger">
                Kein Zertifikat eingerichtet: <span x-text="tls.error"></span>
              </p>
              <p x-show="!tls.error" x-cloak class="hint">
                Der verschlüsselte Zugang ist abgeschaltet (<code>--https-port 0</code>).
              </p>
              <p class="hint">
                Der QR-Scan über die Kamera bleibt dadurch gesperrt. Der Weg über ein Foto in der
                Geräte-Ansicht funktioniert unabhängig davon.
              </p>
            </div>
          </template>
        </div>
```

- [ ] **Step 5: Tests laufen lassen**

Run: `uv run pytest tests/api/test_web.py -v`
Expected: PASS, alle Tests.

- [ ] **Step 6: Committen**

```bash
git add src/loxmatter/web/index.html src/loxmatter/web/app.js tests/api/test_web.py
git commit -m "feat(web): Zertifikatszustand und CA-Download in der Systemansicht

Samt der drei iOS-Schritte inklusive Zertifikatsvertrauen - ohne den
dritten bleibt ein installiertes Profil wirkungslos, und niemand sieht
woran es liegt. Die Schwaeche steht dort, wo geklickt wird: wer die CA
installiert, vertraut ihr fuer jede Adresse.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: README und Handabnahme

**Files:**
- Modify: `README.md` (Abschnitt „Dauerhaft betreiben: `loxmatter run`")

**Interfaces:**
- Consumes: alles Vorherige.
- Produces: nichts.

- [ ] **Step 1: Gesamte Suite grün**

```bash
uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src
```

Erwartet: keine Fehler. **Erst wenn das steht, weitermachen.**

- [ ] **Step 2: README ergänzen**

In `README.md`, nach dem Absatz, der mit „Ohne Token wird die Fabric-Sicherung nicht ausgeliefert" beginnt und vor dem Verweis auf `deploy/testhost/docker-compose.yml`, einfügen:

```markdown
**Verschlüsselter Zugang und das Einlernen per QR-Code.** Der Dienst öffnet
zusätzlich zum HTTP-Port einen HTTPS-Port (`--https-port`, Standard 8443,
`0` schaltet ihn ab) und liefert dort dieselbe Oberfläche aus. Beim ersten
Start erzeugt er dafür in `--tls-dir` (Standard: `tls` neben der Datenbank)
eine eigene Zertifizierungsstelle und ein davon signiertes
Server-Zertifikat; es erneuert sich von selbst, wenn es abläuft oder die
Brücke unter einer anderen IP-Adresse hängt. Scheitert das — fehlendes
`cryptography`, nicht beschreibbares Verzeichnis —, **startet der Dienst
trotzdem**, nur ohne HTTPS und mit einer Warnung im Log.

`/cmd` und `/resync` bleiben davon unberührt auf HTTP: der Miniserver
akzeptiert kein selbstsigniertes Zertifikat. Einen Zwangs-Redirect gibt es
aus demselben Grund nicht.

Der Grund für den ganzen Aufwand ist die Kamera. Browser geben sie nur in
einem *secure context* frei — `localhost` zählt dazu, eine LAN-Adresse wie
`192.168.1.10` **nicht**. Ohne HTTPS bliebe der QR-Sucher auf dem Handy also
gesperrt, also genau dort, wo er gebraucht wird. Der Knopf „QR-Code scannen"
in der Geräte-Ansicht bietet deshalb über HTTP zuerst den Wechsel auf die
verschlüsselte Adresse an und öffnet den Sucher erst dort. Ein erkannter
Code startet das Einlernen sofort und bleibt sichtbar im Feld stehen — bei
einem Thread-Gerät, das ohne Datensatz scheitert, tragen Sie ihn nach und
drücken „Einlernen", ohne erneut zu scannen.

**Es geht auch ganz ohne Zertifikat.** Unter dem Scan-Knopf steht immer ein
Bildweg: fotografieren (auf dem Handy öffnet sich die Kamera-App), eine
Datei wählen, ein Bild hineinziehen oder mit Strg+V einfügen. Dekodiert wird
in beiden Fällen im Browser — das Bild verlässt ihn nie, was über eine
unverschlüsselte Verbindung kein Nebenaspekt ist.

**Das Token gilt je Adresse.** Es liegt im `localStorage` des Browsers, und
der ist an den Ursprung gebunden: nach dem Wechsel von `http://…:8080` auf
`https://…:8443` ist es weg und muss einmal neu eingetragen werden. Das ist
kein Fehler, sondern der Preis dafür, es nicht durch die URL zu schleusen
(dort stünde es in Server-Logs, Proxy-Logs und der Browser-History). Bleiben
Sie danach auf der verschlüsselten Adresse — dort geht es nicht mehr im
Klartext über das Netz, anders als auf dem HTTP-Zugang.

**Damit ein Handy die Warnung nicht bei jedem Aufruf zeigt** — und weil iOS
die Kamera möglicherweise auch nach weggeklickter Warnung sperrt —, bietet
die System-Ansicht das Zertifikat der Brücke unter `GET /ca.crt` zum
Herunterladen an, samt der drei Schritte zum Einrichten. Auf iOS ist der
dritte der entscheidende und meistübersehene: *Einstellungen → Allgemein →
Info → Zertifikatsvertrauen*. Ohne ihn bleibt das installierte Profil
wirkungslos.

**Diese Route ist bewusst offen, und das ist eine Schwäche.** `/ca.crt`
verlangt kein Token und ist über HTTP erreichbar — sie muss es sein, denn
man lädt sie genau dann, *bevor* man dem verschlüsselten Zugang vertraut.
Wer dieses Zertifikat installiert, vertraut der Zertifizierungsstelle
danach für **jede** Adresse, nicht nur für diese Brücke. Und da es
unverschlüsselt zu Ihnen kommt, könnte jemand, der im selben Netz
dazwischenfunkt, an dieser Stelle eine eigene unterschieben und sich
anschließend dauerhaft für beliebige Seiten ausgeben. Das ist ein echter
Zugewinn an Angriffsfläche gegenüber einem reinen HTTP-Betrieb; er wird
eingegangen, weil ein öffentlich vertrautes Zertifikat eine Domain und
Internetzugang verlangte, die diese Brücke nicht voraussetzen darf. Wer
diesen Tausch nicht will, setzt `--https-port 0` und benutzt den Bildweg.
```

Außerdem im Abschnitt „Stand" den Satz zu den gebauten Phasen um einen Hinweis ergänzen:

```markdown
Neu hinzugekommen: ein zweiter, TLS-gesicherter Listener und das Einlernen
per QR-Code (Entwurf:
[`docs/superpowers/specs/2026-09-03-qr-einlernen-https-design.md`](docs/superpowers/specs/2026-09-03-qr-einlernen-https-design.md)).
Die Live-Kamera auf iOS ist dabei **nicht** am Gerät verifiziert — siehe
Abschnitt 9 des Entwurfs.
```

- [ ] **Step 3: Handabnahme — die vier Punkte aus Entwurf Abschnitt 9**

Diese Punkte sind **nicht automatisierbar** und Teil des Entwurfs, nicht Beiwerk. Dienst gegen einen echten matter-server starten, dann der Reihe nach:

1. **Android:** `http://<ip>:8080` öffnen → „QR-Code scannen" → wechseln → Warnung durchklicken → Token neu eintragen → Sucher öffnet sich → echten Gerätecode erkennen → Gerät erscheint in der Liste.
2. **iPhone:** derselbe Weg. Gibt Safari die Kamera nach der weggeklickten Warnung **nicht** frei, `/ca.crt` laden, Profil installieren, Zertifikatsvertrauen einschalten, wiederholen. Scheitert auch das, ist die Live-Kamera auf iOS nicht erreichbar — dann trägt der Bildweg, und das gehört so in die README.
3. **Bildweg auf beiden Geräten über HTTP**, ganz ohne Zertifikat.
4. **Thread-Gerät:** Scan schlägt fehl, der Code bleibt im Feld stehen, Datensatz nachtragen, „Einlernen" gelingt.

Das Ergebnis von Punkt 2 in die README eintragen — als belegte Tatsache, nicht als Vermutung.

- [ ] **Step 4: Committen**

```bash
git add README.md
git commit -m "docs: HTTPS, QR-Einlernen und die Grenzen beider beschreiben

Inklusive der Schwaeche, die /ca.crt mit sich bringt: wer das
Zertifikat installiert, vertraut ihm fuer jede Adresse, und es kommt
unverschluesselt. Wer diesen Tausch nicht will, setzt --https-port 0.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Abdeckung des Entwurfs

| Entwurfsabschnitt | Task |
|---|---|
| 3 Ablauf, Sucher, Sicherungen | 5 |
| 3 Bildweg (Foto, Datei, Drag & Drop, Strg+V) | 4 |
| 3 Code bleibt nach Fehlschlag stehen | 4 (`acceptScannedCode`) |
| 3.1 Keine Payload-Auswertung | 4 (Kommentar an `MATTER_CODE_PATTERN`) |
| 4.1 Zwei Listener auf einer App | 3 |
| 4.2 Signal-Fallstrick | 3 |
| 4.3 Neue Optionen | 3 |
| 4.4 CA und Server-Zertifikat, SAN, Erneuerung | 1 |
| 4.5 TLS verhindert den Start nicht | 1 (`prepare_tls`), 3 |
| 5 `/ca.crt`, `/api/diagnostics/tls`, Anleitung | 2, 6 |
| 5 Die benannte Schwäche | 6 (Oberfläche), 7 (README) |
| 6 Token beim Origin-Wechsel | 5 |
| 7 Dekodierung im Browser, jsQR vendort | 4 |
| 8 Fehlerbehandlung, inkl. „keine Kamera → kein Knopf" | 4, 5 |
| 9 Automatisierte Prüfungen | 1, 2, 3, 4, 5, 6 |
| 9 Handabnahme | 7 |

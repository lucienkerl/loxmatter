"""SQLite-Ablage fuer Geraete und Signale.

Der Schluessel eines Signals ist die Verdrahtung in Loxone (Spec 6.2). Er wird
einmal vergeben und danach nie geaendert — weder beim Umbenennen noch bei einem
erneuten Einlesen desselben Geraets. Deshalb liegt er in einer Datenbank und
nicht in einer Ableitung zur Laufzeit.

device_id wird nie wiederverwendet: ein entferntes und neu eingelerntes Geraet
bekommt neue Schluessel, damit es keine alte Verdrahtung stillschweigend erbt.

Schluesselformat (Spec 6.2): ``d<device_id>_<endpoint>_<slug>``, z. B.
``d12_1_temp``. Zwei Signale auf demselben Endpoint koennen denselben
Profil-Slug tragen (z. B. mehrere Events desselben Clusters, die zufaellig
gleich benannt sind, oder ein generischer Slug fuer zwei unbekannte
Attribute) — in dem Fall haengt ``_assign_key`` die Element-ID an
(``d12_1_temp_5``), um die von der Tabelle ``signal.key`` erzwungene
Eindeutigkeit zu erhalten, ohne den Schluessel eines schon vergebenen
Signals zu aendern.

Eine Store-Instanz gehoert genau einem Thread und genau einer Event-Loop -
`sqlite3.Connection` ist ohne `check_same_thread=False` an ihren Erzeuger-
Thread gebunden, und dieses Modul weicht davon bewusst nicht ab.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from loxmatter.export.commands import DeviceCommand
from loxmatter.matter.discovery import extract_signals
from loxmatter.matter.models import NodeSnapshot, SignalKind, SignalRef
from loxmatter.profiles.table import Exportability, lookup

DEFAULT_UDP_PORT = 7000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS device (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    unique_id  TEXT NOT NULL,
    node_id    INTEGER NOT NULL,
    label      TEXT NOT NULL,
    udp_port   INTEGER NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS signal (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id     INTEGER NOT NULL REFERENCES device(id),
    endpoint      INTEGER NOT NULL,
    cluster_id    INTEGER NOT NULL,
    element_id    INTEGER NOT NULL,
    kind          TEXT NOT NULL,
    key           TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL,
    unit          TEXT NOT NULL,
    exportability TEXT NOT NULL,
    UNIQUE (device_id, endpoint, cluster_id, element_id, kind)
);
CREATE TABLE IF NOT EXISTS command (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   INTEGER NOT NULL REFERENCES device(id),
    node_id     INTEGER NOT NULL,
    endpoint    INTEGER NOT NULL,
    cluster_id  INTEGER NOT NULL,
    command_id  INTEGER NOT NULL,
    key         TEXT NOT NULL UNIQUE,
    slug        TEXT NOT NULL,
    takes_value INTEGER NOT NULL,
    UNIQUE (device_id, endpoint, cluster_id, command_id)
);
"""


@dataclass(frozen=True)
class StoredSignal:
    key: str
    ref: SignalRef
    title: str
    unit: str
    exportability: Exportability


@dataclass(frozen=True)
class StoredCommand:
    key: str
    slug: str
    node_id: int
    endpoint: int
    cluster_id: int
    command_id: int
    takes_value: bool


class UnknownCommandError(KeyError):
    """`KeyError.__str__` haengt die Nachricht in `repr()` ein, wodurch
    `str(exc)` zusaetzliche Anfuehrungszeichen um den ganzen deutschen Text
    legt — Task 6 macht daraus einen HTTP-Fehlerkoerper, dem die Klammerung
    nicht anzusehen sein soll. Die Unterklasse gibt die Nachricht
    unveraendert zurueck; `pytest.raises(KeyError, ...)` faengt sie weiterhin,
    da sie von `KeyError` erbt."""

    def __str__(self) -> str:
        return str(self.args[0])


class Store:
    def __init__(self, path: Path | str) -> None:
        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def _device_identity(self, snapshot: NodeSnapshot) -> str:
        """Faellt auf die Node-ID zurueck: manche Geraete melden keine UniqueID (Spec 7.2)."""
        return snapshot.unique_id or f"node:{snapshot.node_id}"

    def register_device(self, snapshot: NodeSnapshot) -> int:
        identity = self._device_identity(snapshot)
        row = self._db.execute(
            "SELECT id FROM device WHERE unique_id = ? AND active = 1", (identity,)
        ).fetchone()
        if row is not None:
            return int(row["id"])

        label = f"{snapshot.vendor_name} {snapshot.product_name}".strip() or identity
        cur = self._db.execute(
            "INSERT INTO device (unique_id, node_id, label, udp_port) VALUES (?, ?, ?, ?)",
            (identity, snapshot.node_id, label, DEFAULT_UDP_PORT),
        )
        self._db.commit()
        device_id = cur.lastrowid
        assert device_id is not None
        return int(device_id)

    def forget_device(self, device_id: int) -> None:
        """Markiert ein Geraet als entfernt. Die id bleibt vergeben (Spec 6.2)."""
        self._db.execute("UPDATE device SET active = 0 WHERE id = ?", (device_id,))
        self._db.commit()

    def udp_port(self, device_id: int) -> int:
        row = self._db.execute("SELECT udp_port FROM device WHERE id = ?", (device_id,)).fetchone()
        if row is None:
            raise KeyError(f"unbekanntes Geraet {device_id}")
        return int(row["udp_port"])

    def device_id_for_node(self, node_id: int) -> int | None:
        """Bildet eine Matter-Node-ID auf die zugehoerige, stabile `device_id` ab.

        Fuer die Laufzeit (Task 8): eine eingehende Subscription von
        matter-server traegt nur die Node-ID, aber die Signalschluessel
        haengen an der `device_id` (siehe Modul-Docstring - eine Node-ID kann
        sich aendern, die `device_id` nie). `None`, wenn kein aktives Geraet
        mit dieser Node-ID bekannt ist, etwa weil es noch nie exportiert oder
        inzwischen entfernt (`forget_device`) wurde - eine Node-ID eines
        entfernten Geraets darf nicht auf dessen alte, inaktive `device_id`
        zeigen.
        """
        row = self._db.execute(
            "SELECT id FROM device WHERE node_id = ? AND active = 1", (node_id,)
        ).fetchone()
        return int(row["id"]) if row is not None else None

    def _existing_keys(self, device_id: int) -> set[str]:
        rows = self._db.execute(
            "SELECT key FROM signal WHERE device_id = ?", (device_id,)
        ).fetchall()
        return {str(r["key"]) for r in rows}

    def _assign_key(self, device_id: int, ref: SignalRef, slug: str, taken: set[str]) -> str:
        """Vergibt einen neuen, noch nicht belegten Schluessel fuer ``ref``.

        Der Normalfall ist ``d<device_id>_<endpoint>_<slug>``. Kollidiert das
        mit einem bereits vergebenen Schluessel eines *anderen* Signals
        desselben Geraets, wird die Element-ID angehaengt — das ist der
        einzige zusaetzliche Diskriminator, der garantiert pro Endpoint
        eindeutig ist (siehe Modul-Docstring, Spec 6.2).
        """
        base = f"d{device_id}_{ref.endpoint}_{slug}"
        if base not in taken:
            return base
        disambiguated = f"{base}_{ref.element_id}"
        if disambiguated in taken:
            raise ValueError(
                f"Schluessel-Kollision fuer Geraet {device_id}: {disambiguated!r} bereits vergeben"
            )
        return disambiguated

    def register_signals(self, device_id: int, snapshot: NodeSnapshot) -> list[StoredSignal]:
        """Legt neue Signale an; bekannte behalten Schluessel und Titel, aber
        `unit` und `exportability` werden bei jedem Aufruf neu bestimmt.

        Spec 6.2 verlangt Unveraenderlichkeit ausdruecklich nur fuer den
        Schluessel — nicht fuer `unit` oder `exportability`. Wuerden diese
        beim ersten Einlernen eingefroren, bliebe ein Signal, das nur meldet,
        weil gerade kein Kommissionierungsfenster offen ist, oder ein
        Attribut wie `StartUpOnOff`, das ein Geraet erst spaeter befuellt,
        fuer immer bei `exportability=none` stehen — und eine Korrektur in
        `clusters.yaml` erreichte ein schon gespeichertes Signal nie. Die
        einzige Abhilfe waere das Loeschen der ganzen Datenbank, was jeden
        Schluessel zerstoert. `title` dagegen bleibt unangetastet, sobald
        `set_title` es einmal gesetzt hat — ab dann gehoert es dem Nutzer
        (siehe `test_key_survives_a_title_change`).

        Laeuft als eine Transaktion: scheitert die Schluesselvergabe fuer ein
        einzelnes neues Signal (siehe `_assign_key`), wird die gesamte
        Registrierung zurueckgerollt statt das Geraet mit einer Teilmenge
        seiner Signale zu belassen. Absichtlich kein `INSERT OR IGNORE` — das
        wuerde eine echte Schluessel-Kollision nicht melden, sondern das
        zweite Signal stillschweigend verwerfen (siehe Modul-Docstring).
        """
        taken = self._existing_keys(device_id)
        try:
            for ref in extract_signals(snapshot):
                profile = lookup(ref, snapshot.attributes.get(ref.path))
                existing = self._db.execute(
                    "SELECT key FROM signal WHERE device_id = ? AND endpoint = ? AND cluster_id = ?"
                    " AND element_id = ? AND kind = ?",
                    (device_id, ref.endpoint, ref.cluster_id, ref.element_id, ref.kind.value),
                ).fetchone()
                if existing is not None:
                    self._db.execute(
                        "UPDATE signal SET unit = ?, exportability = ? WHERE key = ?",
                        (profile.unit, profile.exportability.value, existing["key"]),
                    )
                    continue

                key = self._assign_key(device_id, ref, profile.slug, taken)
                taken.add(key)
                self._db.execute(
                    "INSERT INTO signal "
                    "(device_id, endpoint, cluster_id, element_id, kind, key, title, unit,"
                    " exportability) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        device_id,
                        ref.endpoint,
                        ref.cluster_id,
                        ref.element_id,
                        ref.kind.value,
                        key,
                        profile.slug,
                        profile.unit,
                        profile.exportability.value,
                    ),
                )
        except (ValueError, sqlite3.Error):
            self._db.rollback()
            raise
        self._db.commit()
        return self.signals(device_id)

    def set_title(self, key: str, title: str) -> None:
        self._db.execute("UPDATE signal SET title = ? WHERE key = ?", (title, key))
        self._db.commit()

    def signals(self, device_id: int) -> list[StoredSignal]:
        rows = self._db.execute(
            "SELECT * FROM signal WHERE device_id = ?"
            " ORDER BY endpoint, cluster_id, element_id, kind",
            (device_id,),
        ).fetchall()
        return [
            StoredSignal(
                key=r["key"],
                ref=SignalRef(
                    r["endpoint"], r["cluster_id"], r["element_id"], SignalKind(r["kind"])
                ),
                title=r["title"],
                unit=r["unit"],
                exportability=Exportability(r["exportability"]),
            )
            for r in rows
        ]

    def _existing_command_keys(self, device_id: int) -> set[str]:
        rows = self._db.execute(
            "SELECT key FROM command WHERE device_id = ?", (device_id,)
        ).fetchall()
        return {str(r["key"]) for r in rows}

    def register_commands(
        self, device_id: int, commands: Sequence[DeviceCommand], node_id: int
    ) -> list[StoredCommand]:
        """Macht die exportierten Kommando-Schluessel zur Laufzeit aufloesbar.

        Ohne das schreibt der Exporter Schluessel in die Vorlage, die spaeter
        niemand zurueck auf ein Matter-Kommando abbilden kann. Der Schluessel
        wird ausschliesslich hier zusammengesetzt — der Exporter (cli.py)
        uebernimmt das Ergebnis, statt ihn ein zweites Mal selbst zu bauen.
        Zwei Stellen, die denselben Schluessel unabhaengig zusammensetzen,
        wuerden sonst auseinanderdriften, ohne dass ein Fehler es meldet.

        Ein schon bekanntes Kommando (gleiches device_id/endpoint/cluster_id/
        command_id) behaelt seinen Schluessel, aber `takes_value` und `slug`
        werden bei jedem Aufruf neu uebernommen — genau wie `register_signals`
        `unit` und `exportability` neu bestimmt. Sonst erreichte eine
        Korrektur in `clusters.yaml` (ein Kommando, das nachtraeglich einen
        Wert erwartet, oder umbenannt wird) ein schon gespeichertes Kommando
        nie, und die einzige Abhilfe waere das Loeschen der ganzen Datenbank,
        was jeden Schluessel zerstoert.

        Laeuft als eine Transaktion: scheitert die Schluesselvergabe fuer ein
        einzelnes neues Kommando, wird die gesamte Registrierung
        zurueckgerollt statt das Geraet mit einer Teilmenge seiner Kommandos
        zu belassen. Absichtlich kein `INSERT OR IGNORE` — das wuerde eine
        echte Schluessel-Kollision nicht melden, sondern das zweite Kommando
        stillschweigend verwerfen (siehe Modul-Docstring und
        `register_signals`). Anders als bei Signalen gibt es hier keine
        Ausweichstrategie ueber eine zusaetzliche ID: zwei Kommandos
        verschiedener Cluster auf demselben Endpoint mit gleichem Slug sind
        ein Fehler in `clusters.yaml`, keine ordnungsgemaesse Mehrdeutigkeit.
        """
        taken = self._existing_command_keys(device_id)
        try:
            for command in commands:
                existing = self._db.execute(
                    "SELECT key FROM command WHERE device_id = ? AND endpoint = ?"
                    " AND cluster_id = ? AND command_id = ?",
                    (device_id, command.endpoint, command.cluster_id, command.command_id),
                ).fetchone()
                if existing is not None:
                    self._db.execute(
                        "UPDATE command SET takes_value = ?, slug = ? WHERE key = ?",
                        (int(command.takes_value), command.slug, existing["key"]),
                    )
                    continue

                key = f"d{device_id}_{command.endpoint}_{command.slug}"
                if key in taken:
                    collision = self._db.execute(
                        "SELECT cluster_id, command_id FROM command"
                        " WHERE device_id = ? AND key = ?",
                        (device_id, key),
                    ).fetchone()
                    raise ValueError(
                        f"Schluessel-Kollision fuer Geraet {device_id}: Kommando "
                        f"(cluster_id={command.cluster_id}, command_id={command.command_id}) "
                        f"und (cluster_id={collision['cluster_id']}, "
                        f"command_id={collision['command_id']}) teilen sich den "
                        f"Schluessel {key!r}"
                    )
                taken.add(key)
                self._db.execute(
                    "INSERT INTO command "
                    "(device_id, node_id, endpoint, cluster_id, command_id, key, slug,"
                    " takes_value) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        device_id,
                        node_id,
                        command.endpoint,
                        command.cluster_id,
                        command.command_id,
                        key,
                        command.slug,
                        int(command.takes_value),
                    ),
                )
        except (ValueError, sqlite3.Error):
            self._db.rollback()
            raise
        self._db.commit()
        return self.commands(device_id)

    def commands(self, device_id: int) -> list[StoredCommand]:
        rows = self._db.execute(
            "SELECT * FROM command WHERE device_id = ? ORDER BY endpoint, cluster_id, command_id",
            (device_id,),
        ).fetchall()
        return [self._as_command(r) for r in rows]

    def resolve_command(self, key: str) -> StoredCommand:
        row = self._db.execute("SELECT * FROM command WHERE key = ?", (key,)).fetchone()
        if row is None:
            raise UnknownCommandError(f"unbekannter Kommando-Schluessel {key!r}")
        return self._as_command(row)

    @staticmethod
    def _as_command(row: sqlite3.Row) -> StoredCommand:
        return StoredCommand(
            key=row["key"],
            slug=row["slug"],
            node_id=int(row["node_id"]),
            endpoint=int(row["endpoint"]),
            cluster_id=int(row["cluster_id"]),
            command_id=int(row["command_id"]),
            takes_value=bool(row["takes_value"]),
        )

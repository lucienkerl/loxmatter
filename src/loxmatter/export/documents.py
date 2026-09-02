"""Setzt die beiden Vorlagentypen aus Spec 6.1 zusammen.

Ein VirtualInUdp traegt beliebig viele Befehle, ein Import bringt damit alle
Signale eines Geraets auf einmal ins Projekt. Eine Datei je Geraet — bei 200
Eingaengen in einem Objekt waere die Config nicht mehr navigierbar (Spec 6.2).

Die Attributnamen und ihre Defaults stammen aus dem verifizierten Schema in
Spec 6.1. Sie sind nicht frei waehlbar.

Spec 6.1, „Korrektur 2026-09-02": das Schema stammte urspruenglich aus einer
fremden Referenzimplementierung und wich in vier Punkten von dem ab, was
Loxone Config an 26 realen Vorlagen tatsaechlich schreibt — belegt, nicht
vermutet. Diese Task zieht die vier Korrekturen nach:

1. Jede Vorlage traegt ein `<Info>` als erstes Kind. `templateType` ist `1`
   fuer `VirtualInUdp`, `3` fuer `VirtualOut`. `minVersion="14040925"` ist fuer
   beide der niedrigste an den 26 Vorlagen beobachtete Wert — er gate also die
   wenigsten Config-Versionen. Ob Loxone Config diesen Wert wirklich
   akzeptiert, entscheidet nicht dieser Code, sondern der Import-Beleg in
   Task 7 Schritt 6.
2. `VirtualInUdpCmd` hat 15 Attribute, u. a. `Unit` (Formatstring, Spec 7.3)
   und `HintText`.
3. `VirtualOut` traegt `HintText` zwischen `CmdInit` und `CloseAfterSend`.
4. `VirtualOutCmd` hat 15 Attribute, kein `ID`, und `CmdOnMethod`/`CmdOffMethod`
   stehen zusammen statt verteilt.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from loxmatter.export.signals import LoxoneInput
from loxmatter.export.xml import render_document

_UMLAUTS = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}

# Niedrigster an den 26 realen Vorlagen (Spec 6.1) beobachteter Wert je
# Vorlagentyp — gate damit die wenigsten Config-Versionen aus. Der eigentliche
# Beleg, dass Loxone Config diesen Wert akzeptiert, ist der Import in Task 7.
_MIN_VERSION = "14040925"


@dataclass(frozen=True)
class LoxoneCommand:
    key: str
    title: str
    path: str
    analog: bool


def _flag(value: bool) -> str:
    return "true" if value else "false"


def render_virtual_in_udp(
    device_label: str,
    bridge_ip: str,
    port: int,
    inputs: Sequence[LoxoneInput],
) -> bytes:
    info = ("Info", [("templateType", "1"), ("minVersion", _MIN_VERSION)])
    children = [
        (
            "VirtualInUdpCmd",
            [
                ("Title", entry.title),
                ("Comment", entry.comment),
                ("Address", ""),
                ("Check", f"{entry.key}:\\v"),
                ("Signed", "true"),
                ("Analog", _flag(entry.analog)),
                ("SourceValLow", "0"),
                ("DestValLow", "0"),
                ("SourceValHigh", "100"),
                ("DestValHigh", "100"),
                ("DefVal", "0"),
                ("MinVal", "-2147483647"),
                ("MaxVal", "2147483647"),
                ("Unit", entry.unit_format),
                ("HintText", ""),
            ],
        )
        for entry in inputs
    ]
    return render_document(
        "VirtualInUdp",
        [
            ("Title", f"Matter — {device_label}"),
            ("Comment", "erzeugt von loxmatter"),
            ("Address", bridge_ip),
            ("Port", str(port)),
        ],
        [info, *children],
    )


def render_virtual_out(
    device_label: str,
    base_url: str,
    commands: Sequence[LoxoneCommand],
) -> bytes:
    info = ("Info", [("templateType", "3"), ("minVersion", _MIN_VERSION)])
    children = [
        (
            "VirtualOutCmd",
            [
                ("Title", command.title),
                ("Comment", command.key),
                ("CmdOnMethod", "GET"),
                ("CmdOffMethod", "GET"),
                ("CmdOn", command.path),
                ("CmdOnHTTP", ""),
                ("CmdOnPost", ""),
                ("CmdOff", ""),
                ("CmdOffHTTP", ""),
                ("CmdOffPost", ""),
                ("CmdAnswer", ""),
                ("HintText", ""),
                ("Analog", _flag(command.analog)),
                ("Repeat", "0"),
                ("RepeatRate", "0"),
            ],
        )
        for command in commands
    ]
    return render_document(
        "VirtualOut",
        [
            ("Title", f"Matter — {device_label}"),
            ("Comment", "erzeugt von loxmatter"),
            ("Address", base_url),
            ("CmdInit", ""),
            ("HintText", ""),
            ("CloseAfterSend", "true"),
            ("CmdSep", ""),
        ],
        [info, *children],
    )


def render_system_templates(bridge_ip: str, port: int) -> tuple[bytes, bytes]:
    """Die beiden Vorlagen, die zu keinem Geraet gehoeren.

    bridge_alive ist der Watchdog (Spec 6.5): er toggelt, solange die Bridge
    laeuft, und deckt "Container tot" wie "Netz weg" gleichermassen ab.

    /resync gehoert im Config-Projekt an den Systemstart-Baustein (Spec 6.4).
    UDP ist zustandslos - ohne diesen Aufruf stehen nach einem Neustart des
    Miniservers alle Eingaenge auf ihrem Defaultwert, bei einem Temperatursensor
    womoeglich stundenlang.
    """
    viu = render_virtual_in_udp(
        "System",
        bridge_ip,
        port,
        [
            LoxoneInput(
                key="bridge_alive",
                title="Bridge erreichbar",
                comment="Watchdog: toggelt, solange die Bridge laeuft",
                analog=False,
                unit_format="",
            )
        ],
    )
    vo = render_virtual_out(
        "System",
        f"http://{bridge_ip}:8080",
        [
            LoxoneCommand(
                key="resync",
                title="Alle Werte neu senden",
                path="/resync",
                analog=False,
            )
        ],
    )
    return viu, vo


def filename_for(prefix: str, device_id: int, device_label: str) -> str:
    """Dateiname nach Spec 6.1, auf ASCII normalisiert.

    `device_id` ist nicht Dekoration — er ist der einzige Teil des Namens,
    der Eindeutigkeit garantiert. `Store` vergibt ihn unveraenderlich und
    verwendet ihn nirgends doppelt (siehe `export.signals`); die Normalisierung
    unten dagegen ist verlustbehaftet und bildet absichtlich viele
    unterschiedliche Labels ("Lampe 1", "Lampe_1", "Lampe-1", "厨房", "")
    auf denselben oder einen leeren String ab. Ohne die Geraete-ID wuerden
    zwei Geraete mit kollidierendem Label sich beim Export gegenseitig
    ueberschreiben — der Nutzer importiert dann eine Vorlage im Glauben, es
    seien zwei. Also: die ID hier NICHT entfernen, auch wenn sie im Namen
    redundant zum Label aussieht.

    Das Label bleibt trotzdem im Namen — es macht die Datei fuer einen
    Menschen wiedererkennbar, waehrend die ID sie eindeutig macht.
    """
    text = "".join(_UMLAUTS.get(char, char) for char in device_label)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    safe = "".join(char if char.isalnum() else "_" for char in text)
    while "__" in safe:
        safe = safe.replace("__", "_")
    safe = safe.strip("_")
    stem = f"{prefix}_d{device_id}"
    if safe:
        stem = f"{stem}_{safe}"
    return f"{stem}.xml"

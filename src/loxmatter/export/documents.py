# loxmatter - connects Matter devices to a Loxone Miniserver.
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

"""Assembles the two template types from spec 6.1.

A VirtualInUdp carries any number of commands, so one import brings all of
a device's signals into the project at once. One file per device — with
200 inputs in a single object, the config would no longer be navigable
(spec 6.2).

The attribute names and their defaults come from the verified schema in
spec 6.1. They are not freely chosen.

Spec 6.1, "correction 2026-09-02": the schema originally came from a
third-party reference implementation and differed in four points from
what Loxone Config actually writes in 26 real templates — documented, not
guessed. This task applies the four corrections:

1. Every template carries an `<Info>` as its first child. `templateType`
   is `1` for `VirtualInUdp`, `3` for `VirtualOut`. `minVersion="14040925"`
   is, for both, the lowest value observed across the 26 templates — so it
   gates out the fewest config versions. Whether Loxone Config actually
   accepts this value is not decided by this code but by the import proof
   in task 7 step 6.
2. `VirtualInUdpCmd` has 15 attributes, including `Unit` (format string,
   spec 7.3) and `HintText`.
3. `VirtualOut` carries `HintText` between `CmdInit` and `CloseAfterSend`.
4. `VirtualOutCmd` has 15 attributes, no `ID`, and `CmdOnMethod`/
   `CmdOffMethod` sit together instead of spread apart.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from loxmatter import i18n
from loxmatter.export.signals import LoxoneInput
from loxmatter.export.xml import render_document

_UMLAUTS = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}

# Lowest value observed per template type across the 26 real templates
# (spec 6.1) — that gates out the fewest config versions. The actual proof
# that Loxone Config accepts this value is the import in task 7.
_MIN_VERSION = "14040925"


@dataclass(frozen=True)
class LoxoneCommand:
    """A virtual output, as it ends up in the template.

    `off_path` is the second command of the same output. Loxone provides
    `CmdOn` AND `CmdOff` for a digital virtual output: ONE object that
    sends the one on the rising edge and the other on the falling edge.
    That is exactly what is needed to wire a switch directly onto it - two
    separate outputs for on and off would first have to be tied back
    together by hand in the config.

    Empty if there is no counter-command (`toggle`, or any command with a
    value such as `level`). Then `CmdOff` stays empty, as before.
    """

    key: str
    title: str
    path: str
    analog: bool
    off_path: str = ""


def _flag(value: bool) -> str:
    return "true" if value else "false"


def virtual_in_udp_cmd_attributes(entry: LoxoneInput) -> list[tuple[str, str]]:
    """Attributes of a single `VirtualInUdpCmd` — factored out of
    `render_virtual_in_udp` so that `projectsync.schema` can reuse the same
    attribute list, already verified against a real import, for objects
    newly inserted into the project file, instead of inventing it a second
    time."""
    return [
        ("Title", entry.title),
        ("Comment", entry.comment),
        ("Address", ""),
        ("Check", f"{entry.key}:{entry.check_suffix}"),
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
    ]


def render_virtual_in_udp(
    device_label: str,
    bridge_ip: str,
    port: int,
    inputs: Sequence[LoxoneInput],
) -> bytes:
    info = ("Info", [("templateType", "1"), ("minVersion", _MIN_VERSION)])
    children = [("VirtualInUdpCmd", virtual_in_udp_cmd_attributes(entry)) for entry in inputs]
    return render_document(
        "VirtualInUdp",
        [
            ("Title", f"Matter — {device_label}"),
            ("Comment", i18n.t("export.comment_generated")),
            ("Address", bridge_ip),
            ("Port", str(port)),
        ],
        [info, *children],
    )


def virtual_out_cmd_attributes(command: LoxoneCommand) -> list[tuple[str, str]]:
    """The attributes of a virtual output, in the form Loxone Config itself
    writes them.

    Documented against a template that Config produced after a working
    import (`tests/fixtures/loxone/VO_working.xml`, supplied by the
    user, 2026-09-03). Two rules are embedded in it, and both were
    previously wrong:

    **`Analog="false"` exactly when an off command is set.** That is the
    digital output, where Config sets the "use as digital output" checkbox
    and only then offers the field for the off command at all. An output
    with only one command - `on`, `off`, `toggle` - carries
    `Analog="true"`.

    So it depends on the off command, NOT on whether the command takes a
    value. That was exactly the bug: `onoff` did not arrive with the
    checkbox set, and `CmdOff` stayed ineffective.

    **The four scaling attributes only for the analog output.** Config
    writes `SourceValLow`/`DestValLow`/`SourceValHigh`/`DestValHigh` for
    every output without an off command and leaves them out entirely for
    the digital one.

    The older `VO_reference.xml` contradicts this on the `Analog` value. It
    is a hand-cleaned derivative; this file comes unmodified from Config -
    when in doubt, Config wins. The reference remains valid for everything
    else (attribute names, order, document structure).
    """
    digital = bool(command.off_path)
    attributes: list[tuple[str, str]] = [
        ("Title", command.title),
        ("Comment", command.key),
        ("CmdOnMethod", "GET"),
        ("CmdOffMethod", "GET"),
        ("CmdOn", command.path),
        ("CmdOnHTTP", ""),
        ("CmdOnPost", ""),
        ("CmdOff", command.off_path),
        ("CmdOffHTTP", ""),
        ("CmdOffPost", ""),
        ("CmdAnswer", ""),
        ("Analog", _flag(not digital)),
        ("Repeat", "0"),
        ("RepeatRate", "0"),
    ]
    if not digital:
        attributes += [
            ("SourceValLow", "0"),
            ("DestValLow", "0"),
            ("SourceValHigh", "0"),
            ("DestValHigh", "0"),
        ]
    # `HintText` comes last, not in the middle - Config writes it that way
    # too.
    attributes.append(("HintText", ""))
    return attributes


def output_title(label: str) -> str:
    """The `Title` of a virtual output, for the template AND for the
    container the project sync creates.

    One helper for both: the string used to exist as two separate format
    literals, here and in `projectsync.schema.new_output_container_open_tag`,
    which agreed only by hand. Groups would have made that four.
    """
    return f"Matter — {label}"


def group_output_title(label: str) -> str:
    return i18n.t("export.group_title", label=label)


def render_virtual_out(
    device_label: str,
    base_url: str,
    commands: Sequence[LoxoneCommand],
    *,
    is_group: bool = False,
) -> bytes:
    info = ("Info", [("templateType", "3"), ("minVersion", _MIN_VERSION)])
    children = [("VirtualOutCmd", virtual_out_cmd_attributes(command)) for command in commands]
    return render_document(
        "VirtualOut",
        [
            # Order as in the template Loxone Config itself writes
            # (tests/fixtures/loxone/VO_working.xml):
            # `HintText` sits at the front there, not after `CmdInit`.
            ("HintText", ""),
            ("Title", group_output_title(device_label) if is_group else output_title(device_label)),
            ("Comment", i18n.t("export.comment_generated")),
            ("Address", base_url),
            ("CmdInit", ""),
            ("CloseAfterSend", "true"),
            ("CmdSep", ""),
        ],
        [info, *children],
    )


def render_system_templates(bridge_ip: str, port: int, listen_port: int) -> tuple[bytes, bytes]:
    """The two templates that belong to no device.

    bridge_alive is the watchdog (spec 6.5): it toggles for as long as the
    bridge is running, and covers "container dead" and "network gone"
    alike.

    /resync belongs, in the Config project, on the system-start block
    (spec 6.4). UDP is stateless - without this call, after a Miniserver
    restart all inputs sit at their default value, for a temperature
    sensor possibly for hours.

    `listen_port` is the HTTP port on which `loxmatter run` accepts the
    commands from Loxone (review fix I3, 2026-09-02: previously hard-wired
    here to 8080, independent of `run --listen`; a differing port made
    `/resync` in the Config project run into nothing, without the
    Miniserver ever reporting it - it does not evaluate a virtual output's
    response).
    """
    viu = render_virtual_in_udp(
        "System",
        bridge_ip,
        port,
        [
            LoxoneInput(
                key="bridge_alive",
                title=i18n.t("export.system.bridge_alive_title"),
                comment=i18n.t("export.system.bridge_alive_comment"),
                # Analog like every state (2026-09-03): the watchdog lives
                # precisely off the value CHANGING between 1 and 0. A
                # digital input does not evaluate the value - it would only
                # see that a pattern matches, and so could not notice the
                # change at all. But that is exactly what it needs to: if
                # the value stops changing, the bridge is dead.
                analog=True,
                unit_format="",
            )
        ],
    )
    vo = render_virtual_out(
        "System",
        f"http://{bridge_ip}:{listen_port}",
        [
            LoxoneCommand(
                key="resync",
                title=i18n.t("export.system.resync_title"),
                path="/resync",
                analog=False,
            )
        ],
    )
    return viu, vo


def filename_for(prefix: str, owner_id: int, owner_label: str, *, kind: str = "d") -> str:
    """Filename per spec 6.1, normalised to ASCII.

    `kind` distinguishes a device (`d`, the default and the shape every
    existing export already has) from a group (`g`, design 2026-09-10,
    section 7). It is a parameter rather than a second function because
    everything below it - the lossy normalisation, and the reason the ID
    must stay in the name - applies identically to both.

    `owner_id` is not decoration — it is the only part of the name that
    guarantees uniqueness. `Store` assigns it immutably and never reuses
    it (see `export.signals`); the normalisation below, by contrast, is
    lossy and deliberately maps many different labels ("Lamp 1", "Lamp_1",
    "Lamp-1", "厨房", "") onto the same or an empty string. Without the
    owner ID, two devices (or two groups) with a colliding label would
    overwrite each other's export — the user would then import one
    template believing there were two. So: do NOT remove the ID here,
    even though it looks redundant with the label in the name.

    The label stays in the name nonetheless — it makes the file
    recognisable to a human, while the ID makes it unique.
    """
    text = "".join(_UMLAUTS.get(char, char) for char in owner_label)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    safe = "".join(char if char.isalnum() else "_" for char in text)
    while "__" in safe:
        safe = safe.replace("__", "_")
    safe = safe.strip("_")
    stem = f"{prefix}_{kind}{owner_id}"
    if safe:
        stem = f"{stem}_{safe}"
    return f"{stem}.xml"

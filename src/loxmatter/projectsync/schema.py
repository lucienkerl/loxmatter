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

"""Attribute schema of the project file's objects (design section 3.4/6).

Two separate tiers with different levels of certainty:

**Updating existing objects** (`desired_*_attrs`, `MANAGED_*_ATTRS`)
deliberately touches only the title, the check/CmdOn key itself, the
analog flag and the unit - scaling, MinVal/MaxVal and any wiring remain
untouched, even if an export in the meantime suggests a different value.
This is the low-risk half: it only changes attribute values within a
structure already accepted by Config.

**Creating new objects** (`new_*_open_tag`, `new_cmd_children_xml`,
`new_*_container_open_tag`) builds on the attribute lists from
`export.documents` that are already verified against a real import (see
that module's docstring) - for the child elements (`Co`/`IoData`/
`Display`), which have no counterpart in the template schema, no such
verification exists; that is the unverified remainder that design
section 6 openly names."""

from __future__ import annotations

import re

from loxmatter.export.documents import (
    LoxoneCommand,
    group_output_title,
    output_title,
    virtual_in_udp_cmd_attributes,
    virtual_out_cmd_attributes,
)
from loxmatter.export.signals import LoxoneInput
from loxmatter.export.xml import render_attrs
from loxmatter.projectsync.ids import new_unique_id
from loxmatter.projectsync.scan import Element, parse_attrs

# `Unit` is deliberately NOT here (correction after the user report "the
# unit is no longer there on the virtual inputs", 2026-09-05): in a real
# project file, not a single `<C>` object carries a `Unit` attribute
# (checked against all 3710 in the reference file) - the unit lives
# exclusively in the `<Display>` child, see `new_cmd_children_xml`. A
# `Unit` maintained here would write it to a place Loxone Config never
# reads, and would make every analog input show up as "updated" again on
# every run.
MANAGED_INPUT_CMD_ATTRS: tuple[str, ...] = ("Title", "Check", "Analog")
MANAGED_OUTPUT_CMD_ATTRS: tuple[str, ...] = ("Title", "CmdOn", "CmdOff", "Analog")

_IODATA = re.compile(r"<IoData\s+([^/]*)/>")


def desired_input_cmd_attrs(entry: LoxoneInput) -> dict[str, str]:
    """Desired state of the attributes managed by the update for an existing
    `VirtualUdpInCmd` (design section 5) - without `Unit`, see
    `MANAGED_INPUT_CMD_ATTRS`."""
    return {
        "Title": entry.title,
        "Check": f"{entry.key}:{entry.check_suffix}",
        "Analog": "true" if entry.analog else "false",
    }


def desired_output_cmd_attrs(command: LoxoneCommand) -> dict[str, str]:
    """Desired state of the attributes managed by the update for an existing
    `VirtualOutCmd`. `CmdOff` is deliberately absent when there is no off
    command - a missing attribute is never treated by `diff.py` as "must
    be removed", only present attributes are compared."""
    attrs = {
        "Title": command.title,
        "CmdOn": command.path,
        "Analog": "false" if command.off_path else "true",
    }
    if command.off_path:
        attrs["CmdOff"] = command.off_path
    return attrs


def new_input_cmd_open_tag(entry: LoxoneInput, iname: str, u: str) -> str:
    """Start tag of a freshly created `VirtualUdpInCmd`, on the same
    attributes as the already-verified template file (`export.documents.
    virtual_in_udp_cmd_attributes`), extended with `Type`/`IName`/`V`/`U`/
    `Nio`/`WF`, which a project file additionally needs.

    **`V="178"` (correction after a real-world test, 2026-09-05):** a
    previously missing mandatory field - checked against the real
    reference file, ALL 3710 `<C>` objects there carry a `V` attribute
    without exception (almost always `"178"`, only the `Document` root
    object itself carries the full Loxone Config version number). Without
    `V`, `_new_device_edit` did create the device container visibly, but
    its command children stayed empty in Loxone Config - the bug the user
    reported, which led to the check against the real file.

    **Without `Unit` (correction after a user report, 2026-09-05):** the
    template file carries the unit as an attribute, a project file does
    not - there it sits in the `<Display>` child (`new_cmd_children_xml`),
    and not a single `<C>` object in the reference file carries a `Unit`
    attribute. It is therefore filtered back out of the adopted template
    attribute list here; otherwise the unit would land in a place Loxone
    Config never reads, and would be missing at the input."""
    attrs = [
        ("Type", "VirtualUdpInCmd"),
        ("IName", iname),
        ("V", "178"),
        ("U", u),
        *((name, value) for name, value in virtual_in_udp_cmd_attributes(entry) if name != "Unit"),
        ("Nio", "2"),
        ("WF", "16400"),
    ]
    return f"<C {render_attrs(attrs)}>"


def new_output_cmd_open_tag(command: LoxoneCommand, iname: str, u: str) -> str:
    """Like `new_input_cmd_open_tag`, for `VirtualOutCmd` - on
    `export.documents.virtual_out_cmd_attributes`, likewise with `V="178"`."""
    attrs = [
        ("Type", "VirtualOutCmd"),
        ("IName", iname),
        ("V", "178"),
        ("U", u),
        *virtual_out_cmd_attributes(command),
        ("Nio", "1"),
        ("WF", "16400"),
    ]
    return f"<C {render_attrs(attrs)}>"


def new_input_container_open_tag(
    device_label: str, bridge_ip: str, port: int, iname: str, u: str
) -> str:
    """Start tag of a freshly created `VirtualUdpIn` device container - only
    for the experimental path (design section 3.4). Since the correction
    above, this too carries `V="178"`, like every other `<C>` node in the
    real reference file."""
    attrs = [
        ("Type", "VirtualUdpIn"),
        ("IName", iname),
        ("V", "178"),
        ("U", u),
        ("Title", f"Matter — {device_label}"),
        ("WF", "16384"),
        ("Address", bridge_ip),
        ("Port", str(port)),
    ]
    return f"<C {render_attrs(attrs)}>"


def new_output_container_open_tag(
    device_label: str, base_url: str, iname: str, u: str, *, is_group: bool = False
) -> str:
    """Like `new_input_container_open_tag`, for `VirtualOut`.

    `is_group` picks the title only (`group_output_title` vs.
    `output_title`, design 2026-09-10 section 7) - a group container is
    otherwise identical to a device one: outputs only, same attributes.
    """
    attrs = [
        ("Type", "VirtualOut"),
        ("IName", iname),
        ("V", "178"),
        ("U", u),
        ("Title", group_output_title(device_label) if is_group else output_title(device_label)),
        ("WF", "16384"),
        ("Address", base_url),
        ("CloseAfterSend", "true"),
        ("CmdSep", ";"),
    ]
    return f"<C {render_attrs(attrs)}>"


def new_caption_open_tag(kind: str, u: str) -> str:
    """Start tag of a freshly created `VirtualInCaption`/`VirtualOutCaption`
    - only when the project file has never had a virtual input or output
    of this kind before (design section 8: the special case of creating
    one, also behind the experimental flag).

    **Correction after a real-world test (2026-09-05):** all four
    `VirtualInCaption`/`VirtualOutCaption` objects in the real reference
    file carry NO `IName` (unlike originally assumed - the `C<n>` naming
    pattern belongs to other object types), but they do carry `V="178"`
    and a fixed `Title` (`"Virtuelle Eingänge"`/`"Virtuelle Ausgänge"`,
    just as Loxone Config itself labels newly created captions) plus
    `WF="16384"`, like the device containers below them. No more `iname`
    parameter - a caption does not need one."""
    if kind not in ("input", "output"):
        raise ValueError(f"Unknown kind {kind!r} - expected 'input' or 'output'.")
    type_name = "VirtualInCaption" if kind == "input" else "VirtualOutCaption"
    title = "Virtuelle Eingänge" if kind == "input" else "Virtuelle Ausgänge"
    attrs = [
        ("Type", type_name),
        ("V", "178"),
        ("U", u),
        ("Title", title),
        ("WF", "16384"),
    ]
    return f"<C {render_attrs(attrs)}>"


def sibling_iodata_attrs(text: str, element: Element) -> dict[str, str] | None:
    """The attributes of the `<IoData .../>` child of an existing cmd
    element, if present - the source for the permission values of a newly
    created sibling object (design section 6: the same Cr/Pr values as a
    neighbouring object, instead of inventing them)."""
    if element.self_closing or element.inner_end is None:
        return None
    match = _IODATA.search(text, element.open_end, element.inner_end)
    if match is None:
        return None
    return parse_attrs(match.group(0))


def find_any_iodata_attrs(text: str, caption: Element | None) -> dict[str, str] | None:
    """Like `sibling_iodata_attrs`, but searched across the entire content
    of a `VirtualInCaption`/`VirtualOutCaption` container - a fallback for
    a completely new device that has no sibling cmd yet."""
    if caption is None or caption.self_closing or caption.inner_end is None:
        return None
    match = _IODATA.search(text, caption.open_end, caption.inner_end)
    if match is None:
        return None
    return parse_attrs(match.group(0))


_DEFAULT_UNIT_FORMAT = "<v.1>"


def new_cmd_children_xml(
    *,
    kind: str,
    existing_u: set[str],
    iodata_attrs: dict[str, str] | None,
    analog: bool = False,
    unit_format: str = "",
) -> str:
    """XML text of the child elements of a freshly created cmd object:
    wiring stubs (two for an input - `AQ`/`Q` -, one for an output - `I`),
    optionally an `IoData` element with adopted permission values, and a
    `Display` element (design section 6). `kind` is ``"input"`` or
    ``"output"``.

    **`analog`/`unit_format` (correction after the user report "the unit
    is no longer there on the virtual inputs", 2026-09-05):** the
    `Display` element is the only place a project file carries the unit -
    as a complete format string including the unit text (`<v.3> kW`),
    accompanied by `Type="2"` for an analog value. That is how it appears
    on all 86 analog inputs in the reference file; a fixed `Unit="<v.1>"`
    as before threw away every signal's unit. If `unit_format` is empty
    (an analog signal with no known unit, see `profiles.table.
    unit_format`), the bare format string remains - an empty `Unit=""`
    does not occur anywhere in the reference file."""
    if kind == "input":
        connectors = [
            f'<Co K="AQ" U="{new_unique_id(existing_u)}"/>',
            f'<Co K="Q" U="{new_unique_id(existing_u)}"/>',
        ]
    elif kind == "output":
        connectors = [f'<Co K="I" U="{new_unique_id(existing_u)}"/>']
    else:
        raise ValueError(f"Unknown kind {kind!r} - expected 'input' or 'output'.")

    display_attrs: list[tuple[str, str]] = []
    if analog:
        # `Type="2"` appears in the reference file without exception on
        # analog values - digital ones have none, hence no fixed value here.
        display_attrs.append(("Type", "2"))
    display_attrs.append(("Unit", unit_format or _DEFAULT_UNIT_FORMAT))
    display_attrs.append(("StateOnly", "true"))

    parts = list(connectors)
    if iodata_attrs:
        parts.append(f"<IoData {render_attrs(list(iodata_attrs.items()))}/>")
    parts.append(f"<Display {render_attrs(display_attrs)}/>")
    return "".join(parts)

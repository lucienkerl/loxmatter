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

"""Rooms in the patched file - design 2026-09-29, sections 5 and 6."""

from __future__ import annotations

import dataclasses
import re

from loxmatter.export.signals import SignalKind
from loxmatter.model.store import SignalRef, StoredDevice, StoredSignal
from loxmatter.profiles.table import Exportability
from loxmatter.projectsync.diff import build_plan
from loxmatter.projectsync.index import build_index
from loxmatter.projectsync.patch import apply_plan

KITCHEN_U = "3000-0002-0000-aaaaaaaaaaaaaaaa"


def _signal(key: str, device_id: int, title: str = "Ein/Aus", unit: str = "") -> StoredSignal:
    return StoredSignal(
        key=key,
        ref=SignalRef(endpoint=1, cluster_id=6, element_id=0, kind=SignalKind.ATTRIBUTE),
        title=title,
        unit=unit,
        exportability=Exportability.DIGITAL,
        device_id=device_id,
        exported=True,
        functional=True,
        resend=False,
    )


def _device(device_id: int, label: str) -> StoredDevice:
    return StoredDevice(
        id=device_id,
        technology="matter",
        address=str(device_id),
        unique_id=f"u{device_id}",
        label=label,
        exported_at=None,
        updated_at=None,
        room=None,
        device_types=None,
        network_features=None,
        vendor_name=None,
        product_name=None,
        firmware=None,
        serial_number=None,
    )


def _patch_bytes(index, device, signals, *, commands=()):
    commands = list(commands)
    plan = build_plan(index, [device], {device.id: signals}, {device.id: commands})
    return apply_plan(
        index,
        plan,
        [device],
        {device.id: signals},
        {device.id: commands},
        bridge_ip="10.0.0.5",
        port=7000,
        listen=8080,
    )


def _patch(index, device, signals, *, commands=()):
    return _patch_bytes(index, device, signals, commands=commands).decode("utf-8-sig")


def _cmd_xml(patched: str, key: str) -> str:
    """The whole `<C ...>...</C>` text of the cmd whose Check/CmdOn names `key`."""
    match = re.search(
        rf'<C Type="Virtual\w+Cmd"[^>]*{re.escape(key)}[^>]*>.*?</C>', patched, re.DOTALL
    )
    assert match is not None, key
    return match.group(0)


def _places(patched: str) -> list[str]:
    return re.findall(r'<C Type="Place"[^>]*/>', patched)


def _right_groups(patched: str) -> list[str]:
    return re.findall(r'<C Type="RightGroup"[^>]*/>', patched)


def _attr(tag: str, name: str) -> str:
    match = re.search(rf'\b{name}="([^"]*)"', tag)
    assert match is not None, (tag, name)
    return match.group(1)


def test_a_new_signal_goes_into_the_matching_room(rooms_project):
    index = build_index(rooms_project)
    device = dataclasses.replace(_device(1, "Altes Geraet"), room="küche")
    signals = [_signal("d1_1_onoff", 1), _signal("d1_1_temp", 1, title="Temperatur")]
    patched = _patch(index, device, signals)
    assert f'Pr="{KITCHEN_U}"' in _cmd_xml(patched, "d1_1_temp")
    # The copied category stays.
    assert 'Cr="1000-0005-0000-aaaaaaaaaaaaaaaa"' in _cmd_xml(patched, "d1_1_temp")
    assert len(_places(patched)) == 2  # no room created


def test_an_existing_object_keeps_its_room(rooms_project):
    index = build_index(rooms_project)
    device = dataclasses.replace(_device(1, "Altes Geraet"), room="Küche")
    signals = [_signal("d1_1_onoff", 1), _signal("d1_1_temp", 1, title="Temperatur")]
    patched = _patch(index, device, signals)
    assert 'Pr="1000-0006-0000-aaaaaaaaaaaaaaaa"' in _cmd_xml(patched, "d1_1_onoff")


def test_a_missing_room_is_created_with_its_rights_group(rooms_project):
    index = build_index(rooms_project)
    device = dataclasses.replace(_device(2, "Neues Geraet"), room="Werkstatt")
    patched = _patch(index, device, [_signal("d2_1_onoff", 2)])

    places = _places(patched)
    rights = _right_groups(patched)
    assert len(places) == 3 and len(rights) == 3
    new_place, new_rights = places[-1], rights[-1]
    assert _attr(new_place, "Title") == "Werkstatt"
    assert _attr(new_rights, "Title") == "Werkstatt"
    assert _attr(new_place, "RGR") == _attr(new_rights, "U")
    # PType comes from the ordinary room, not from the default room (`First="true"`).
    # Icon is left to Loxone Config.
    assert _attr(new_place, "PType") == "3"
    assert "Icon=" not in new_place
    assert 'First="true"' not in new_place
    # Every cmd of the new device points at the new room.
    for key in ("d2_1_onoff", "d2_online"):
        assert f'Pr="{_attr(new_place, "U")}"' in _cmd_xml(patched, key)


def test_a_room_shared_by_two_devices_is_created_once(rooms_project):
    index = build_index(rooms_project)
    devices = [
        dataclasses.replace(_device(2, "Zwei"), room="Werkstatt"),
        dataclasses.replace(_device(3, "Drei"), room="werkstatt"),
    ]
    signals = {2: [_signal("d2_1_onoff", 2)], 3: [_signal("d3_1_onoff", 3)]}
    commands = {2: [], 3: []}
    plan = build_plan(index, devices, signals, commands)
    patched = apply_plan(
        index, plan, devices, signals, commands, bridge_ip="10.0.0.5", port=7000, listen=8080
    ).decode("utf-8-sig")
    werkstatt = [p for p in _places(patched) if _attr(p, "Title") == "Werkstatt"]
    assert len(werkstatt) == 1
    for key in ("d2_1_onoff", "d3_1_onoff"):
        assert f'Pr="{_attr(werkstatt[0], "U")}"' in _cmd_xml(patched, key)


def test_next_obj_counts_the_room_objects(rooms_project):
    index = build_index(rooms_project)
    device = dataclasses.replace(_device(1, "Altes Geraet"), room="Werkstatt")
    signals = [_signal("d1_1_onoff", 1), _signal("d1_1_temp", 1, title="Temperatur")]
    patched = _patch(index, device, signals)
    # One new cmd + one Place + one RightGroup.
    assert 'NextObj="103"' in patched


def test_an_old_project_gets_a_room_without_rights_group(places_only_project):
    index = build_index(places_only_project)
    device = dataclasses.replace(_device(1, "Altes Geraet"), room="Werkstatt")
    signals = [_signal("d1_1_onoff", 1), _signal("d1_1_temp", 1, title="Temperatur")]
    patched = _patch(index, device, signals)
    new_place = _places(patched)[-1]
    assert _attr(new_place, "Title") == "Werkstatt"
    assert "RGR=" not in new_place
    assert _right_groups(patched) == []
    assert 'NextObj="102"' in patched


def test_without_a_room_list_new_objects_keep_the_neighbours_room(sample_project):
    index = build_index(sample_project)
    device = dataclasses.replace(_device(1, "Altes Geraet"), room="Werkstatt")
    signals = [_signal("d1_1_onoff", 1), _signal("d1_1_temp", 1, title="Temperatur")]
    patched = _patch(index, device, signals)
    assert 'Pr="1000-0006-0000-aaaaaaaaaaaaaaaa"' in _cmd_xml(patched, "d1_1_temp")
    assert _places(patched) == []


def test_everything_outside_the_edits_is_byte_identical(rooms_project):
    """Removing exactly the inserted objects and the changed counter gives
    back the input (the same guarantee `test_patch.py` checks for the other
    edits)."""
    index = build_index(rooms_project)
    device = dataclasses.replace(_device(1, "Altes Geraet"), room="Werkstatt")
    signals = [_signal("d1_1_onoff", 1), _signal("d1_1_temp", 1, title="Temperatur")]
    patched = _patch(index, device, signals)
    stripped = patched
    stripped = stripped.replace(_places(patched)[-1], "")
    stripped = stripped.replace(_right_groups(patched)[-1], "")
    stripped = stripped.replace(_cmd_xml(patched, "d1_1_temp"), "")
    stripped = stripped.replace('NextObj="103"', 'NextObj="100"')
    assert stripped == rooms_project

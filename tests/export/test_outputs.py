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


"""Pairing of on and off into one combined virtual output."""

from __future__ import annotations

from dataclasses import replace

from loxmatter.export.outputs import LUMITECH_TITLE, PAIRED_TITLE, to_outputs
from loxmatter.model.store import StoredCommand


def command(
    key: str, slug: str, *, endpoint: int = 1, cluster_id: int = 6, takes_value: bool = False
) -> StoredCommand:
    return StoredCommand(
        key=key,
        slug=slug,
        technology="matter",
        address="3",
        endpoint=endpoint,
        cluster_id=cluster_id,
        command_id=0,
        takes_value=takes_value,
        device_id=1,
    )


def test_on_and_off_also_yield_one_combined_output():
    """Loxone knows CmdOn AND CmdOff for a digital output - a switch can be
    wired to that directly, without first linking the two together by hand
    in the Config."""
    outputs = to_outputs([command("d1_1_on", "on"), command("d1_1_off", "off")])
    combined = next(o for o in outputs if o.title == PAIRED_TITLE)
    assert combined.path == "/cmd/d1_1_on/1"
    assert combined.off_path == "/cmd/d1_1_off/1"
    assert combined.analog is False


def test_the_separate_outputs_survive_alongside_the_combined_one():
    """Not an either-or: if a device can also be switched outside of Loxone,
    the state in the Config no longer follows the actual one - then you want
    to trigger on and off individually, instead of hanging on an edge that
    might never come."""
    outputs = to_outputs([command("d1_1_on", "on"), command("d1_1_off", "off")])
    assert [o.title for o in outputs] == [PAIRED_TITLE, "on", "off"]
    for single in (o for o in outputs if o.title != PAIRED_TITLE):
        assert single.off_path == ""


def test_pairing_needs_the_same_endpoint_and_cluster():
    """The case that would silently go wrong: a power strip has `on` and
    `off` per socket. If pairing crossed endpoints, one output would switch
    socket 1 on and socket 2 off."""
    outputs = to_outputs([command("d1_1_on", "on"), command("d1_2_off", "off", endpoint=2)])
    assert all(o.title != PAIRED_TITLE for o in outputs)
    assert all(o.off_path == "" for o in outputs)

    across_clusters = to_outputs(
        [command("d1_1_on", "on"), command("d1_1_off", "off", cluster_id=8)]
    )
    assert all(o.title != PAIRED_TITLE for o in across_clusters)


def test_toggle_and_valued_commands_get_no_partner():
    outputs = to_outputs(
        [
            command("d1_1_toggle", "toggle"),
            command("d1_1_level", "level", cluster_id=8, takes_value=True),
        ]
    )
    assert [o.title for o in outputs] == ["toggle", "level"]
    assert [o.analog for o in outputs] == [False, True]
    assert all(o.off_path == "" for o in outputs)


def test_a_lonely_on_without_an_off_stays_alone():
    outputs = to_outputs([command("d1_1_on", "on")])
    assert [o.title for o in outputs] == ["on"]
    assert outputs[0].off_path == ""


def test_an_unexported_command_is_not_an_output():
    """Design 2026-09-24, 4.4: the template, project sync and CLI export
    all take only exported commands - filtered once in `_to_outputs`.

    Fault to prove it: remove the `command.exported` filter at the top of
    `_to_outputs` - `d1_1_color` appears alongside `d1_1_lumitech`."""
    commands = [
        command("d1_1_lumitech", "lumitech", takes_value=True),
        replace(command("d1_1_color", "color", takes_value=True), exported=False),
    ]
    assert [o.key for o in to_outputs(commands)] == ["d1_1_lumitech"]


def test_the_lumitech_output_is_titled_for_what_it_takes():
    """Design 2026-09-24, decision 5: every other output's title is its
    slug, but `lumitech` names a format a Loxone user may not connect with
    the lighting controller - so it gets the descriptive title instead.

    Fault to prove it: use `command.slug` for the title unconditionally -
    the output's title reads "lumitech" instead of "Lumitech / RGB"."""
    [output] = to_outputs([command("d1_1_lumitech", "lumitech", takes_value=True)])
    assert output.title == LUMITECH_TITLE
    assert output.analog is True


def test_on_alone_is_a_plain_output_when_off_is_withheld():
    """Design 2026-09-24, 4.4: the on/off pairing works on the exported
    rows only - with only `on` selected, the output is a plain one
    without an off path.

    Fault to prove it: filter `exported` after building the pairs instead
    of before - `d1_1_on` would still pair with the withheld `d1_1_off`."""
    commands = [
        command("d1_1_on", "on", takes_value=False),
        replace(command("d1_1_off", "off", takes_value=False), exported=False),
    ]
    outputs = to_outputs(commands)
    assert [(o.key, o.off_path) for o in outputs] == [("d1_1_on", "")]

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


"""Turns stored commands into a template's virtual outputs.

The counterpart to `export.signals` for the input side. Deliberately NOT
in `export.commands`: that module derives commands from a Matter snapshot
and so sits below `model.store` (which imports `DeviceCommand` from it).
Accessing `StoredCommand` from there would be an import cycle.
"""

from __future__ import annotations

from collections.abc import Sequence

from loxmatter.export.documents import LoxoneCommand
from loxmatter.model.store import StoredCommand

ON_SLUG = "on"
OFF_SLUG = "off"
PAIRED_TITLE = "onoff"


def _command_path(command: StoredCommand) -> str:
    return f"/cmd/{command.key}/" + ("<v>" if command.takes_value else "1")


def to_outputs(commands: Sequence[StoredCommand]) -> list[LoxoneCommand]:
    """Builds a device's virtual outputs from its commands.

    **On and off are additionally available as ONE combined output**
    (2026-09-03). Loxone provides `CmdOn` and `CmdOff` for a digital
    virtual output: one object that sends the one on the rising edge and
    the other on the falling edge. That is exactly what is needed to wire
    a switch directly onto it.

    **The individual outputs are kept nonetheless**, and that is not
    indecision but the case where the device can also be switched outside
    Loxone: then the state in the config no longer follows the actual
    state, and you want to be able to trigger on and off individually
    instead of depending on an edge that may never come. Having both
    variants in the template costs nothing - they are entries to pick
    from; whoever wires up the combined one simply leaves the individual
    ones unused.

    Only what belongs together is paired: same endpoint AND same cluster.
    The grouping comes from the stored fields, not from the key name -
    keys are opaque (main document 6.2), and inferring the endpoint back
    from `d1_1_on` would be a backdoor violation of that rule.

    `toggle` gets no partner: it has no counter-command. The same applies
    to any command with a value (`level`), which is analog anyway.

    The URLs do not change - `/cmd/d1_1_on/1` and `/cmd/d1_1_off/1` stay
    what they were. All that is new is one additional Loxone object that
    uses both. The combined output sits immediately before its `on`, so
    the three sit together in the config.

    One source for both export paths: `cli.py`'s `export` command and the
    API router used to assemble this list separately, twice over.
    """
    by_group: dict[tuple[int, int], dict[str, StoredCommand]] = {}
    for command in commands:
        by_group.setdefault((command.endpoint, command.cluster_id), {})[command.slug] = command

    pairs: dict[str, StoredCommand] = {}
    for group in by_group.values():
        on, off = group.get(ON_SLUG), group.get(OFF_SLUG)
        if on is not None and off is not None and not on.takes_value and not off.takes_value:
            pairs[on.key] = off

    result: list[LoxoneCommand] = []
    for command in commands:
        off = pairs.get(command.key)
        if off is not None:
            result.append(
                LoxoneCommand(
                    # The comment names both keys: otherwise there is no
                    # way to see in the config which two commands are
                    # combined here.
                    key=f"{command.key} + {off.key}",
                    title=PAIRED_TITLE,
                    path=_command_path(command),
                    analog=False,
                    off_path=_command_path(off),
                )
            )
        result.append(
            LoxoneCommand(
                key=command.key,
                title=command.slug,
                path=_command_path(command),
                analog=command.takes_value,
            )
        )
    return result

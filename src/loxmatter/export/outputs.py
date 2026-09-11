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

from collections.abc import Callable, Sequence
from typing import Protocol

from loxmatter.export.documents import LoxoneCommand
from loxmatter.model.store import StoredCommand, StoredGroupCommand

ON_SLUG = "on"
OFF_SLUG = "off"
PAIRED_TITLE = "onoff"


class OutputCommand(Protocol):
    """What `_to_outputs` needs of a command: a key, a name and whether it
    carries a value. `StoredCommand` and `StoredGroupCommand` both satisfy
    it; the endpoint, which only one of them has, is supplied through
    `pair_key` instead of being read here.

    Declared as read-only properties rather than bare attribute
    annotations on purpose: a Protocol member written as `key: str` names
    an ASSIGNABLE attribute, and a frozen dataclass does not have one of
    those under strict mypy - it would then satisfy neither
    `StoredCommand` nor `StoredGroupCommand`, both frozen.
    """

    @property
    def key(self) -> str: ...
    @property
    def slug(self) -> str: ...
    @property
    def takes_value(self) -> bool: ...


def _command_path(command: OutputCommand) -> str:
    return f"/cmd/{command.key}/" + ("<v>" if command.takes_value else "1")


def _device_pair_key(command: StoredCommand) -> tuple[int, int]:
    return (command.endpoint, command.cluster_id)


def _group_pair_key(command: StoredGroupCommand) -> tuple[int, int]:
    """A group command has no endpoint (design 4.1), so the cluster alone
    decides what belongs together. `0` keeps the tuple shape so both keys
    are the same type."""
    return (0, command.cluster_id)


def _to_outputs[CommandT: OutputCommand](
    commands: Sequence[CommandT], pair_key: Callable[[CommandT], tuple[int, int]]
) -> list[LoxoneCommand]:
    """Builds a set of virtual outputs from stored commands - the shared
    implementation behind `to_outputs` (devices) and `to_group_outputs`
    (groups).

    The type parameter `CommandT`, bound to `OutputCommand`, is what lets
    `_device_pair_key` (only accepts `StoredCommand`) and `_group_pair_key`
    (only `StoredGroupCommand`) each type-check as `pair_key`: it is bound
    to the SAME concrete type as `commands` for a given call, so a plain
    `Callable[[OutputCommand], ...]` parameter - which would reject both,
    since neither function accepts the wider protocol type - is avoided.

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

    Only what belongs together is paired: the caller decides what
    "belongs together" means via `pair_key` - see below. The grouping
    comes from the stored fields, not from the key name - keys are
    opaque (main document 6.2), and inferring the endpoint back from
    `d1_1_on` would be a backdoor violation of that rule.

    `toggle` gets no partner: it has no counter-command. The same applies
    to any command with a value (`level`), which is analog anyway.

    The URLs do not change - `/cmd/d1_1_on/1` and `/cmd/d1_1_off/1` stay
    what they were. All that is new is one additional Loxone object that
    uses both. The combined output sits immediately before its `on`, so
    the three sit together in the config.

    One source for both export paths: `cli.py`'s `export` command and the
    API router used to assemble this list separately, twice over.

    `pair_key` says what "belongs together" means, because a GROUP command
    has no endpoint at all - there the cluster alone decides (design
    2026-09-10, section 7). A parameter rather than a second copy of this
    function: two copies would drift, and they would drift SILENTLY, since
    an unpaired `on` does not raise but simply passes through as its own
    output - the missing off-path would show up only as a switch in Loxone
    that never turns anything off.
    """
    by_group: dict[tuple[int, int], dict[str, CommandT]] = {}
    for command in commands:
        by_group.setdefault(pair_key(command), {})[command.slug] = command

    pairs: dict[str, CommandT] = {}
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


def to_outputs(commands: Sequence[StoredCommand]) -> list[LoxoneCommand]:
    """A device's virtual outputs. See `_to_outputs`; this pairs commands
    by `(endpoint, cluster_id)` - a device command has both."""
    return _to_outputs(commands, _device_pair_key)


def to_group_outputs(commands: Sequence[StoredGroupCommand]) -> list[LoxoneCommand]:
    """A group's virtual outputs. See `_to_outputs`, whose pairing rule
    this only re-parameterises: a group command has no endpoint, so the
    cluster alone decides what belongs together."""
    return _to_outputs(commands, _group_pair_key)

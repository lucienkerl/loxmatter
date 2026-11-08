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

"""A device's configuration values as the web UI shows them (design
2026-11-08, section 6).

Setting one needs no route of its own: an option is a command row, and
`POST /api/commands/{key}` with the value is exactly what Loxone's `/cmd`
does. This module only answers the read: every option with its title, its
range or choices, the value the device last reported and the value still
waiting for a sleeping device.

**Names.** The source names an option in English (`fallback_name`) and
gives a `translation_key` - the one Home Assistant translates. Both look
up `config.option.<key>` in `strings.yaml` first, so the common ones read in
the user's language; an option nobody translated yet shows the source's
English, the way a device's own model name does. A choice reads as its
number and unit when it names one (`_30_seconds` -> "30 s"), else through
`config.choice.<name>`, else as its name with the underscores taken out.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from loxmatter import i18n
from loxmatter.api.models import ConfigChoiceOut, ConfigOptionOut, ConfigOptionsOut
from loxmatter.device_config import Choice, ConfigOption, from_raw
from loxmatter.model.store import StoredCommand, StoredSignal
from loxmatter.sources import SourceNotConfiguredError, Sources


@runtime_checkable
class PendingWriteSource(Protocol):
    """A source that queues configuration values - the Zigbee one."""

    def pending_writes(self, address: str) -> Mapping[tuple[int, int, int], object]: ...


# Units a choice's member name can spell, as the last word of the name.
_UNIT_WORDS: dict[str, str] = {
    "second": "config.unit.seconds",
    "seconds": "config.unit.seconds",
    "sec": "config.unit.seconds",
    "s": "config.unit.seconds",
    "minute": "config.unit.minutes",
    "minutes": "config.unit.minutes",
    "min": "config.unit.minutes",
    "hour": "config.unit.hours",
    "hours": "config.unit.hours",
    "h": "config.unit.hours",
}


def _translated(key: str) -> str | None:
    return i18n.t(key) if i18n.has(key) else None


def option_title(option: ConfigOption) -> str:
    for candidate in (option.translation_key, option.name):
        if candidate:
            title = _translated(f"config.option.{candidate}")
            if title is not None:
                return title
    return option.fallback_title


def _number_text(number: float) -> str:
    return str(int(number)) if float(number).is_integer() else str(number)


def choice_label(option: ConfigOption, choice: Choice) -> str:
    words = [word for word in choice.name.lower().split("_") if word]
    if choice.number is not None and words:
        unit_key = _UNIT_WORDS.get(words[-1])
        if unit_key is not None:
            return f"{_number_text(choice.number)} {i18n.t(unit_key)}"
    translated = _translated(f"config.choice.{choice.name.lower()}")
    if translated is not None:
        return translated
    text = " ".join(words)
    return text[:1].upper() + text[1:] if text else str(choice.raw)


def _unit_text(unit: str) -> str:
    key = _UNIT_WORDS.get(unit.lower())
    return i18n.t(key) if key is not None else unit


def options_out(
    commands: list[StoredCommand],
    signals: list[StoredSignal],
    values: Mapping[str, float | bool],
    pending: Mapping[tuple[int, int, int], object],
) -> ConfigOptionsOut:
    """Every option of one device, in the order its rows are stored."""
    signal_keys = {
        (signal.ref.endpoint, signal.ref.cluster_id, signal.ref.element_id): signal.key
        for signal in signals
        if signal.ref.kind.value == "attribute"
    }
    out: list[ConfigOptionOut] = []
    for command in commands:
        option = command.option
        if option is None:
            continue
        identity = (option.endpoint, option.cluster_id, option.attribute_id)
        signal_key = signal_keys.get(identity)
        current = values.get(signal_key) if signal_key is not None else None
        waiting = pending.get(identity)
        out.append(
            ConfigOptionOut(
                key=command.key,
                name=option.name,
                title=option_title(option),
                kind=option.kind,
                unit=_unit_text(option.unit),
                minimum=option.minimum,
                maximum=option.maximum,
                step=option.step,
                choices=[
                    ConfigChoiceOut(
                        value=option.choice_value(choice), label=choice_label(option, choice)
                    )
                    for choice in option.choices
                ],
                value=None if current is None else float(current),
                pending=None if waiting is None else from_raw(option, waiting),
                exported=command.exported,
                signal_key=signal_key,
            )
        )
    return ConfigOptionsOut(options=out)


def pending_for(
    sources: Sources | None, technology: str, address: str
) -> Mapping[tuple[int, int, int], object]:
    """What the device's source still owes it; nothing when the source is
    not running - the rows stay on disk and are shown again once it is."""
    if sources is None:
        return {}
    try:
        source = sources.get(technology)
    except SourceNotConfiguredError:
        return {}
    if not isinstance(source, PendingWriteSource):
        return {}
    return source.pending_writes(address)

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

"""Configuration values a device brings along (design 2026-11-08).

A motion sensor's keep time, a presence sensor's sensitivity, an LED
indicator: values that do not describe what the device measures but how it
behaves, and that can be written. A source describes each one as a
`ConfigOption`; everything downstream - the store, the web UI, the Loxone
export, the `/cmd` endpoint - only ever sees this module's vocabulary.

**One value scale, the Loxone one.** An option has exactly one number that
the web UI shows, Loxone sends, and the virtual input reports back:

- a number option shows the device's raw value times its `multiplier`;
- an enum option whose every choice names a distinct number ("30 seconds",
  "60 seconds", ...) is set and reported by that number, so Loxone sends 60
  for a minute and not the enum's position 1. Any other enum is set and
  reported by its raw value, and the export documents which value means
  what;
- a switch is 0 or 1.

`to_raw` and `from_raw` are the only two places that cross between the two
scales.

**An option travels as a command row.** Writing an option IS what Loxone
does to it, so it is stored in the `command` table and gets a key, an
`exported` flag and a virtual output like every command. Its command ID is
negative - no Matter or Zigbee command has one - and encodes the attribute
it writes (`option_command_id`), the same trick `LUMITECH` plays with its
cluster ID. The row carries the option itself as JSON, so a value can be
checked before it is queued even while the radio is away.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Literal

OptionKind = Literal["number", "enum", "switch"]

OPTION_COMMAND_SLUG_PREFIX = "set_"


@dataclass(frozen=True)
class Choice:
    """One value of an enum option.

    `name` is the enum member's own name, as the source declares it
    (`_30_seconds`, `Medium`). `number` is the number that name spells, when
    it spells one - see `_number_in`."""

    raw: int
    name: str
    number: float | None = None


@dataclass(frozen=True)
class ConfigOption:
    """One writable configuration value of one device endpoint."""

    endpoint: int
    cluster_id: int
    attribute_id: int
    # The source's own name for the value (`keep_time`). It is the slug of
    # the option's command and signal, and the key of its title in
    # `strings.yaml` (`config.option.<name>`) when there is one.
    name: str
    kind: OptionKind
    # English, as the source wrote it - shown when `strings.yaml` has no
    # title for `name` and the source's `translation_key`.
    fallback_title: str
    translation_key: str | None = None
    unit: str = ""
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    multiplier: float = 1.0
    choices: tuple[Choice, ...] = field(default_factory=tuple)
    on_raw: int = 1
    off_raw: int = 0

    @property
    def command_id(self) -> int:
        return option_command_id(self.attribute_id)

    @property
    def command_slug(self) -> str:
        return OPTION_COMMAND_SLUG_PREFIX + self.name

    @property
    def path(self) -> str:
        return f"{self.endpoint}/{self.cluster_id}/{self.attribute_id}"

    @property
    def by_number(self) -> bool:
        """Whether an enum option is set by the numbers its choices name."""
        if self.kind != "enum" or not self.choices:
            return False
        numbers = [choice.number for choice in self.choices]
        return None not in numbers and len(set(numbers)) == len(numbers)

    def choice_value(self, choice: Choice) -> float:
        """The value Loxone and the web UI use for one choice."""
        if self.by_number:
            assert choice.number is not None
            return choice.number
        return float(choice.raw)

    def to_json(self) -> str:
        return json.dumps(
            {
                "endpoint": self.endpoint,
                "cluster_id": self.cluster_id,
                "attribute_id": self.attribute_id,
                "name": self.name,
                "kind": self.kind,
                "fallback_title": self.fallback_title,
                "translation_key": self.translation_key,
                "unit": self.unit,
                "minimum": self.minimum,
                "maximum": self.maximum,
                "step": self.step,
                "multiplier": self.multiplier,
                "choices": [[c.raw, c.name, c.number] for c in self.choices],
                "on_raw": self.on_raw,
                "off_raw": self.off_raw,
            },
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, text: str) -> ConfigOption:
        data = json.loads(text)
        return cls(
            endpoint=int(data["endpoint"]),
            cluster_id=int(data["cluster_id"]),
            attribute_id=int(data["attribute_id"]),
            name=str(data["name"]),
            kind=data["kind"],
            fallback_title=str(data["fallback_title"]),
            translation_key=data.get("translation_key"),
            unit=str(data.get("unit") or ""),
            minimum=data.get("minimum"),
            maximum=data.get("maximum"),
            step=data.get("step"),
            multiplier=float(data.get("multiplier") or 1.0),
            choices=tuple(
                Choice(raw=int(raw), name=str(name), number=number)
                for raw, name, number in data.get("choices") or []
            ),
            on_raw=int(data.get("on_raw", 1)),
            off_raw=int(data.get("off_raw", 0)),
        )


class OptionValueError(ValueError):
    """A value the option cannot take. The message is for the person who
    sent it; the caller wraps it into its own error."""


def option_command_id(attribute_id: int) -> int:
    """The command ID an option's row is stored under. Negative, so it
    collides with no real command; `-1 - id` rather than `-id`, so
    attribute 0 is not command 0."""
    return -1 - attribute_id


def is_option_command(cluster_id: int, command_id: int) -> bool:
    """Whether a `(cluster, command)` pair is an option write. `LUMITECH`
    is `(-1, 0)` and is not one: its cluster is negative, an option's never
    is."""
    return cluster_id >= 0 and command_id < 0


def attribute_of_command(command_id: int) -> int:
    return -1 - command_id


_NUMBER = re.compile(r"\d+")


def _number_in(name: str) -> float | None:
    """The number an enum member's name spells, if it spells exactly one:
    `_30_seconds` is 30. `Medium` and `PIR_and_radar` spell none, and
    `_2_5_m` spells two - it gives up rather than guess whether that is
    2.5 or two numbers."""
    found = _NUMBER.findall(name)
    if len(found) != 1:
        return None
    return float(found[0])


def choice(raw: int, name: str) -> Choice:
    return Choice(raw=int(raw), name=name, number=_number_in(name))


def _as_float(value: str | float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise OptionValueError(str(value)) from exc
    if math.isnan(number) or math.isinf(number):
        raise OptionValueError(str(value))
    return number


def to_raw(option: ConfigOption, value: str | float) -> int | float | bool:
    """The device's raw value for a value on the Loxone scale. Raises
    `OptionValueError` for a value the option cannot take - checked here,
    before anything is queued, so a typo in a Loxone output is a 400 and
    not a write the device refuses later."""
    number = _as_float(value)
    if option.kind == "switch":
        if number not in (0.0, 1.0):
            raise OptionValueError(str(value))
        return option.on_raw if number == 1.0 else option.off_raw
    if option.kind == "enum":
        for candidate in option.choices:
            if math.isclose(option.choice_value(candidate), number):
                return candidate.raw
        raise OptionValueError(str(value))
    if option.minimum is not None and number < option.minimum - 1e-9:
        raise OptionValueError(str(value))
    if option.maximum is not None and number > option.maximum + 1e-9:
        raise OptionValueError(str(value))
    raw = number / option.multiplier if option.multiplier else number
    return round(raw)


def from_raw(option: ConfigOption, raw: object) -> float | None:
    """The Loxone-scale value of a raw attribute value, or None when it
    is no value this option knows."""
    if isinstance(raw, bool):
        raw = int(raw)
    if not isinstance(raw, (int, float)):
        return None
    if option.kind == "switch":
        return 1.0 if int(raw) == option.on_raw else 0.0
    if option.kind == "enum":
        for candidate in option.choices:
            if candidate.raw == int(raw):
                return option.choice_value(candidate)
        return float(raw) if not option.by_number else None
    return float(raw) * option.multiplier

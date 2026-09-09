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

"""Translates stored signals into Loxone input objects.

Two rules from the spec shape this module:

Spec 6.3 — a Matter event has no home in Loxone. A virtual UDP input only
knows values. Every event therefore becomes two objects: a digital pulse
that produces the edge, and a monotonic counter that survives a lost UDP
packet, because it then only jumps instead of swallowing it.

Spec 6.6 — lists, structs, null values and text are discarded here. They
remain visible in storage and in the interface, but they cannot become a
Loxone object.

Spec 7.3 — a signal's unit no longer travels in the comment; instead it is
translated into a Loxone format string via `profiles.table.unit_format`
(the `unit_format` field). Digital inputs and events always carry `""`
there: a format string with decimal places makes no sense for a pulse or
a counter.

Spec 6.2 — the device prefix ``d<device_id>`` does not come here from a
guess about the signal list, but from the caller, who knows it from
`Store`. And because an event's counter key (`<key>_n`) is made up freely
here and reserved nowhere, `to_inputs` checks before returning that no key
is assigned twice — otherwise two Loxone objects would share the same UDP
name, and Loxone Config would not report that.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from loxmatter import i18n
from loxmatter.matter.models import SignalKind
from loxmatter.model.store import StoredSignal
from loxmatter.profiles.table import Exportability, unit_format


@dataclass(frozen=True)
class LoxoneInput:
    """A virtual UDP input, as it ends up in the template.

    On `analog` and `check_suffix` (both checked against the Miniserver,
    2026-09-03): a DIGITAL input fires as soon as its detection pattern
    matches - Loxone does not evaluate the value after the colon while
    doing so. That has two consequences that showed up in operation:

    * A state like `onoff` sends `key:1` and `key:0`. A pattern with `\v`
      matches both, so the input was always on.
    * An event sends a pulse, so also `key:1` and shortly after `key:0`.
      A button press thus fired TWICE.

    Hence:

    * **States are analog.** Loxone reads the number, 1 and 0 become
      distinguishable. An analog input with 0/1 drives any block that
      expects a state.
    * **Events stay digital**, but their pattern ends on `:1` instead of
      `:\v`. Only the rising edge matches, the zero is ignored, and the
      pulse fires exactly once. That works only for signals that produce
      a pulse on their own - for a state, the zero would be exactly the
      information that would get lost.
    """

    key: str
    title: str
    comment: str
    analog: bool
    unit_format: str
    # What follows "key:" in the detection pattern. "\\v" reads out the
    # value (states and counters), "1" matches only the rising edge
    # (events).
    check_suffix: str = "\\v"


def to_inputs(
    signals: Sequence[StoredSignal], device_id: int, device_label: str
) -> list[LoxoneInput]:
    """Produces a device's input objects, including the online signal.

    Aborts loudly instead of producing wrongly wired templates:

    - every signal must belong to ``device_id`` (prefix ``d<device_id>_``).
      A signal from a different device in this list is a caller bug and
      must not silently produce a mislabelled device.
    - no key may be assigned twice. An event's counter key (``<key>_n``)
      is made up freely here and reserved nowhere in `Store` — if a later
      `clusters.yaml` slug happened to collide with it, that would be two
      `LoxoneInput`s with an identical key, i.e. two Loxone objects
      listening on the same UDP name.

    ``signal.exported`` decides whether a signal produces a `LoxoneInput`
    at all (review fix important #3, 2026-09-02): previously this function
    filtered exclusively on `exportability`, and the `exported` flag from
    `PATCH /api/signals/{key}` (spec 5) changed the API response but never
    a generated template — turning a signal off in the interface simply
    had no effect on the export. An event with `exported=False` therefore
    produces neither a pulse nor a counter. The device's online signal is
    explicitly unaffected by this: it does not belong to a single signal
    but to the device itself (spec 6.5), and `StoredSignal` carries no
    `exported` flag for it at all.
    """
    prefix = f"d{device_id}_"
    inputs: list[LoxoneInput] = []
    # Key -> description of where it came from, for the message on a
    # collision.
    origins: dict[str, str] = {}

    def emit(entry: LoxoneInput, origin: str) -> None:
        if entry.key in origins:
            raise ValueError(
                f"Key collision during export: {entry.key!r} is produced by both "
                f"{origins[entry.key]} and {origin} — that would result in "
                f"two Loxone objects for the same UDP name."
            )
        origins[entry.key] = origin
        inputs.append(entry)

    for signal in signals:
        if not signal.key.startswith(prefix):
            raise ValueError(
                f"Signal {signal.key!r} does not belong to device {device_id} "
                f"(expected prefix {prefix!r})."
            )

        if not signal.exported:
            continue

        comment = f"{device_label} · {signal.ref.path}"

        if signal.ref.kind is SignalKind.EVENT:
            emit(
                # Digital, but the pattern matches only the rising edge:
                # the pulse thus fires exactly once instead of twice (see
                # LoxoneInput).
                LoxoneInput(
                    signal.key,
                    signal.title,
                    i18n.t("export.signals.pulse_comment_suffix", comment=comment),
                    False,
                    "",
                    check_suffix="1",
                ),
                f"the pulse of {signal.key!r}",
            )
            emit(
                LoxoneInput(
                    f"{signal.key}_n",
                    i18n.t("export.signals.counter_title_suffix", title=signal.title),
                    i18n.t("export.signals.counter_comment_suffix", comment=comment),
                    True,
                    "",
                ),
                f"the counter of {signal.key!r}",
            )
            continue

        if signal.exportability in (Exportability.ANALOG, Exportability.DIGITAL):
            # The boolean is also analog: a digital input could not
            # distinguish 1 and 0 (see LoxoneInput). The unit remains that
            # of the signal - a state has none.
            emit(
                LoxoneInput(signal.key, signal.title, comment, True, unit_format(signal.unit)),
                f"signal {signal.key!r}",
            )

    online_key = f"d{device_id}_online"
    emit(
        # A state, not a pulse - so analog (see LoxoneInput).
        LoxoneInput(
            online_key,
            i18n.t("export.signals.online_title", device_label=device_label),
            device_label,
            True,
            "",
        ),
        "the online signal",
    )
    return inputs

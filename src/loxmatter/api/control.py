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

"""Operating a device from the UI.

This is not a convenience feature (Spec 8.1). If switching a lamp via
Loxone does not work, a click here separates the two possible causes: if
the device reacts, the fault is in the Loxone wiring or the export; if it
does not react, the fault is in Matter, Thread, or the device itself.

The translation comes from `commands.translate` - the same one the Loxone
endpoint uses. A separate copy here would drift, and then the diagnosis
would have exactly the fault it is meant to find (Spec 4.2). The status
codes for `POST /api/commands/{key}` therefore follow the Loxone endpoint
`/cmd/{key}/{value}` from Phase 4 (`loxone/server.py`) verbatim: 404
unknown key, 400 mismatched value, 502 device does not respond.

**Finding on the writability of an attribute (Task 4, 2026-09-02).** The
snapshot (`NodeSnapshot.attributes`) carries only values, no access
rights - "writable" appears nowhere there. Checked against the installed
packages, not guessed:

- `chip.clusters.ClusterObjects.ClusterAttributeDescriptor` (the base
  class of every generated attribute class such as
  `BasicInformation.Attributes.NodeLabel`) carries `cluster_id`,
  `attribute_id`, `attribute_type`, `must_use_timed_write` - no property
  that distinguishes read from write access. `must_use_timed_write` is
  something else: whether an *allowed* write access needs a timed-write
  envelope, not whether it is allowed.
- `matter_server.client.client.MatterClient.write_attribute(node_id,
  attribute_path, value)` does not check anything beforehand - the call
  goes to the controller unchecked; a rejection would come, if at all, as
  an error from the real device, not from matter-server itself.
- **Correction (review fix Important #2, 2026-09-02): a full-text search
  for "writable" did in fact get hits - this previously stated the
  opposite by mistake.** `chip/clusters/CHIPClusters.py` (part of the
  installed `chip` package) carries its own table, independent of
  `ClusterObjects`, with exactly these access rights: `grep -c
  '"writable": True' chip/clusters/CHIPClusters.py` yields 250 hits, and
  for `BasicInformation` (cluster 0x28 = 40) exactly the three attribute
  IDs 5 (NodeLabel), 6 (Location) and 16 (LocalConfigDisabled) are marked
  `"writable": True` in it - exactly the three that `_WRITABLE_ATTRIBUTES`
  below already arrived at independently, against a real device. So the
  information does exist, just not where it was first looked for
  (`ClusterAttributeDescriptor`).
- **This module is nonetheless not importable, and nothing in
  python-matter-server uses it.** `from chip.clusters.CHIPClusters import
  ChipClusters` fails in this distribution with `ImportError: cannot
  import name 'exceptions' from 'chip'` - the package
  `home_assistant_chip_clusters`, which provides `chip.clusters.
  CHIPClusters` here, ships `CHIPClusters.py` without the accompanying
  `chip/exceptions.py` that the file requires on load. A full-text search
  for `CHIPClusters` in the installed `matter_server` package likewise
  yields not a single hit - python-matter-server does not read this table
  anywhere.

In practice, the conclusion therefore remains unchanged, only its
justification is now different: **not because writability appears
nowhere, but because it appears in a table that this installation cannot
load and that python-matter-server itself does not use.** A raw write
attempt against a read-only attribute consequently does not show up here,
but - if at all - only at the device, in a form this bridge could not
reliably distinguish from a connection error. That is exactly what must
not happen with a diagnostic tool: a click that has no effect must arrive
as a clear refusal, not as a silent failure somewhere between bridge and
device.

So the same asymmetry applies here as for commands (Spec 6.7): an
**allowlist** instead of the generous pass-through that applies to the
*export* of signals (Spec 3.5). `_WRITABLE_ATTRIBUTES` below is
deliberately small and populated exclusively with entries that can either
be proven against a real device checked into this test suite (IKEA
GRILLPLATS, `tests/fixtures/nodes/ikea_grillplats_plug.json`) or, clearly
marked as such, rest solely on the Matter specification, without a
matching device available to cross-check - the same restraint as in
`commands/color.py`. A wrongly blocked attribute costs a missing control
option; a wrongly allowed one can misconfigure a device.

**Further open point (see spec, section 12, point 7):** the manually
maintained allowlist does not scale beyond a handful of devices - every
additional writable attribute needs its own entry, backed by a real
device or the specification. Once `chip.clusters.CHIPClusters` becomes
importable in a later version (or parsing the file as data without an
import proves acceptable), this list could be replaced by reading out the
`"writable"` table found above - see spec.

**Open point, deliberately left unresolved here (see spec, section 12):**
even an attribute on the allowlist cannot actually be written as things
stand today - `BridgeMatterClient` (matter/client.py) has no
`write_attribute`, and this module's interface
(`build_control_router(store, invoke, values)`) does not accept a writing
caller for it either; `invoke` is typed exclusively for commands
(`Callable[[MatterCall], Awaitable[None]]`), `values` only reads
(see `ValueReader`), and an attribute write is neither of those.
`POST /api/signals/{key}/write` therefore honestly answers with 501 for
an allowed attribute instead of a success that does nothing - the same
stance as above, just one step further: a response that silently does
nothing is exactly the failure this tool is meant to surface, not
produce.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from fastapi import APIRouter, HTTPException

from loxmatter import i18n
from loxmatter.api.models import CommandOut, ControlRange, ControlsOut, ValueIn
from loxmatter.commands.translate import MatterCall, UnsupportedValueError, to_matter_calls
from loxmatter.model.store import Store, UnknownCommandError, UnknownDeviceError
from loxmatter.profiles.table import command_control, command_slug

Invoker = Callable[[MatterCall], Awaitable[None]]

logger = logging.getLogger(__name__)

# See the module docstring "Finding on the writability of an attribute" for
# the rationale of this list and why it is an allowlist, not a blocklist.
# (cluster ID, attribute ID) pairs.
_WRITABLE_ATTRIBUTES: frozenset[tuple[int, int]] = frozenset(
    {
        # BasicInformation (0/40) - proven against the checked-in IKEA
        # GRILLPLATS template (0/40/5 = "", 0/40/6 = "XX", 0/40/16 = False):
        # all three paths are actually present there, not merely assumed
        # per the specification.
        (40, 5),  # NodeLabel
        (40, 6),  # Location
        (40, 16),  # LocalConfigDisabled
    }
)


def _is_writable(cluster_id: int, attribute_id: int) -> bool:
    return (cluster_id, attribute_id) in _WRITABLE_ATTRIBUTES


class ValueReader(Protocol):
    """What this route needs from `runtime` - reading only.

    Deliberately narrower than `api.devices.RuntimeValues`: the control
    route sets nothing online, and a protocol that demands more than it
    uses forces every test to a bigger double than the case needs.
    `loxone.runtime.Runtime` satisfies both.
    """

    def last_values_for(self, device_id: int) -> dict[str, float | bool]: ...


# ColorTempPhysicalMinMireds / ColorTempPhysicalMaxMireds, checked
# against the installed SDK (chip.clusters.Objects.ColorControl.Attributes).
_CLUSTER_COLOR = 768
_ATTR_CT_PHYS_MIN_MIREDS = 16395
_ATTR_CT_PHYS_MAX_MIREDS = 16396


def build_control_router(store: Store, invoke: Invoker, values: ValueReader) -> APIRouter:
    router = APIRouter(prefix="/api")

    def _require_device(device_id: int) -> None:
        try:
            store.device(device_id)
        except UnknownDeviceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def _kelvin_range(device_id: int, endpoint: int) -> ControlRange | None:
        """The light's color temperature range, in Kelvin - or None.

        Kelvin = 1e6 / mired is a reciprocal: the SMALLER mired yields the
        LARGER Kelvin, so min and max swap when converting.

        None instead of a fallback range when the light does not report
        its limits: a slider that ends at 6500 K even though the device
        stops at 4000 K lets you set a value that it silently clips -
        exactly the silent failure this view is meant to surface
        (spec 8.1).
        """
        wanted = (_ATTR_CT_PHYS_MIN_MIREDS, _ATTR_CT_PHYS_MAX_MIREDS)
        keys = {
            signal.ref.element_id: signal.key
            for signal in store.signals(device_id)
            if signal.ref.endpoint == endpoint
            and signal.ref.cluster_id == _CLUSTER_COLOR
            and signal.ref.element_id in wanted
        }
        current = values.last_values_for(device_id)
        mireds: list[float] = []
        for element_id in wanted:
            key = keys.get(element_id)
            value = current.get(key) if key is not None else None
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                return None
            mireds.append(float(value))

        kelvins = sorted(int(1_000_000 / mired) for mired in mireds)
        return ControlRange(min=kelvins[0], max=kelvins[1])

    @router.get("/devices/{device_id}/controls")
    async def controls(device_id: int) -> ControlsOut:
        """Only named commands become a control (Spec 6.7: output commands
        come from AcceptedCommandList, not from attributes).

        Der Rohexport (`loxmatter export --raw`, `export.commands.
        extract_commands(..., raw=True)`) kann Kommandos ohne Eintrag in
        `clusters.yaml` in den Store schreiben - ihr Slug ist dann ein
        generischer Platzhalter wie `c4_cmd0` (siehe dort). Ein Knopf mit
        dieser Beschriftung waere fuer die Bedienoberflaeche nutzlos: niemand
        weiss, was `c4_cmd0` bewirkt, ohne die Vorlage zu lesen - und ein
        Klick auf einen Knopf ohne erkennbare Bedeutung ist das Gegenteil
        von Spec 8.1s "ein Klick trennt die beiden moeglichen Ursachen".
        `command_slug` ist dieselbe Quelle, die auch `to_matter_calls`
        letztlich bedient (ueber `commands.translate._PAYLOAD_BUILDERS`,
        gegen `clusters.yaml` synchron gehalten von
        `profiles.table.known_command_pairs` - siehe dort) - ein hier
        gefilterter Rohbefehl war ohnehin nie ausfuehrbar, sondern haette
        sofort mit 400 quittiert. Diese Route zeigt deshalb nur, was ein
        Klick tatsaechlich ausloesen kann.

        The filter does not go unnoticed, though (review fix Minor #4,
        2026-09-02): `hidden_raw_commands` counts how many of the device's
        commands were filtered out. Without this number, an unnamed
        device would look in the UI exactly like one with no output
        commands at all (Spec 8.1 - exactly the case that
        `test_button_offers_no_controls` checks) - a person diagnosing a
        foreign device would then be misled by that, instead of seeing:
        there would still be commands, just unnamed.
        """
        _require_device(device_id)
        stored = store.commands(device_id)
        named = []
        for command in stored:
            if command_slug(command.cluster_id, command.command_id) is None:
                continue
            control = command_control(command.cluster_id, command.command_id)
            named.append(
                CommandOut(
                    key=command.key,
                    slug=command.slug,
                    takes_value=command.takes_value,
                    control=control,
                    range=_kelvin_range(device_id, command.endpoint)
                    if control == "kelvin"
                    else None,
                )
            )
        return ControlsOut(commands=named, hidden_raw_commands=len(stored) - len(named))

    @router.post("/commands/{key}")
    async def execute_command(key: str, body: ValueIn) -> dict[str, str]:
        try:
            stored = store.resolve_command(key)
        except UnknownCommandError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        try:
            # The same check, for the same reason, as in `write_signal`
            # below and `PATCH /api/signals/{key}` (api/devices.py) - both
            # carry the name review fix Important #4 from Task 2, closed
            # there so far exclusively for signals: `resolve_command`
            # searches the `command` table alone, without checking the
            # status of the associated device, and `forget_device` does
            # not delete a row there, only sets `device.active = 0`. A
            # command of a removed device therefore remained triggerable
            # via its key - the same gap, now closed for commands (review
            # fix Important #1, 2026-09-02).
            store.device(stored.device_id)
        except UnknownDeviceError as exc:
            raise HTTPException(
                status_code=404,
                detail=i18n.t(
                    "api.errors.command_belongs_to_removed_device",
                    command_key=key,
                    device_id=stored.device_id,
                ),
            ) from exc

        try:
            calls = to_matter_calls(stored, body.value)
        except UnsupportedValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            # Mehrere Aufrufe, weil ein Loxone-Wert mehr als eine Sache
            # bedeuten kann - der Farb-Ausgang traegt Farbe UND Helligkeit
            # (siehe `to_matter_calls`). Der erste Fehlschlag bricht ab und
            # wird gemeldet; ein halb gesetzter Zustand ist dabei moeglich
            # und dort begruendet.
            for call in calls:
                await invoke(call)
        except Exception as exc:  # jedes Geraeteproblem wird zu 502
            # logger.exception schreibt den vollen Traceback ins Server-Log,
            # NICHT in die HTTP-Antwort - dieselbe Begruendung wie beim
            # Loxone-Endpunkt in loxone/server.py.
            logger.exception("Matter-Aufruf fuer Schluessel %r fehlgeschlagen", key)
            raise HTTPException(
                status_code=502, detail=i18n.t("api.errors.device_unreachable", exc=exc)
            ) from exc

        return {"status": "ok", "key": key}

    @router.post("/signals/{key}/write")
    async def write_signal(key: str, body: ValueIn) -> dict[str, str]:
        """Sets an attribute raw (Spec 8, view 2). See the module docstring
        for the finding on writability and the open point that an allowed
        attribute is not actually written yet today."""
        stored = store.signal_by_key(key)
        if stored is None:
            raise HTTPException(
                status_code=404, detail=i18n.t("api.errors.unknown_signal_key", signal_key=key)
            )
        try:
            # The same check as in PATCH /api/signals/{key}
            # (api/devices.py, review fix Important #4): a signal of a
            # removed device remains findable via its key, but should no
            # longer be operable.
            store.device(stored.device_id)
        except UnknownDeviceError as exc:
            raise HTTPException(
                status_code=404,
                detail=i18n.t(
                    "api.errors.signal_belongs_to_removed_device",
                    signal_key=key,
                    device_id=stored.device_id,
                ),
            ) from exc

        if not _is_writable(stored.ref.cluster_id, stored.ref.element_id):
            # Review fix Minor #3, 2026-09-02: this message previously
            # pointed to the module docstring - helpful in a server log,
            # but meaningless for a person who only looks at the UI. The
            # message now says by itself what is going on and what can be
            # done.
            raise HTTPException(
                status_code=400,
                detail=i18n.t("api.control.fail_not_writable", signal_key=key),
            )

        # Allowed, but not yet wired up - see module docstring, paragraph
        # "Open point". A 200 response here would be worse than this
        # honest error: it would fake an effect that does not occur, and
        # that is exactly what this tool is meant to make visible, not
        # hide. Wording likewise review fix Minor #3: no more reference to
        # the module docstring.
        raise HTTPException(
            status_code=501,
            detail=i18n.t("api.control.fail_not_wired", signal_key=key),
        )

    return router

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

"""Response models of the REST API.

Deliberately separate from the storage models in `model.store`: what the
UI sees is a view of the state, not a mapping of the tables. If the schema
changes, the API does not necessarily change along with it.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SignalOut(BaseModel):
    """`exportable`/`reason` (Spec 6.6) and `exported` (user-toggleable, see
    `model.store.StoredSignal.exported`) say what TECHNICALLY fits a
    Loxone input and which OF THOSE should go into the next export -
    `functional` (Task 8) answers a third, independent question: whether
    `profiles.relevance.is_functional` classifies this signal as intended
    for the DEVICE TYPE. The UI uses only this field to divide the signal
    list into "Functional" and "Expert" (`api.devices._signal_out` reads
    it unchanged from `StoredSignal.functional`) - there is no second
    computation of the rule, neither in the API layer nor in JavaScript.

    `resend` (periodic resend design, 2026-09-04) is a FOURTH, again
    independent question: whether the periodic timer (`Runtime.
    resend_marked`) should resend this signal again even without a
    change. Does not affect `/resync` or bridge startup
    (`Runtime.resend_all`) - those deliberately ignore this field, see
    the docstring there."""

    model_config = ConfigDict(frozen=True)

    key: str
    path: str
    kind: str
    title: str
    unit: str
    value: float | bool | str | None
    exportable: bool
    reason: str | None
    exported: bool
    functional: bool
    resend: bool


class DeviceOut(BaseModel):
    """`signal_count`/`exportable_count` say how many signals exist and how
    many of them technically fit a Loxone input (Spec 6.6) - both
    regardless of whether they would actually be exported as a template.
    `next_export_count` (follow-up Fix 7, Phase 6) is the distinct number
    that the device tile did not show at all until then: how many
    `LoxoneInput`s (including the online signal) the next export would
    actually produce (`export.signals.to_inputs`, filtered on `exported`)
    - for the test template's plug, about 159 signals, 110 exportable, but
    only 6 in the next export (5 functional plus the online signal)."""

    model_config = ConfigDict(frozen=True)

    id: int
    node_id: int
    label: str
    online: bool
    signal_count: int
    exportable_count: int
    next_export_count: int
    # Room and category (device-tab design, 2026-09-05). `room` is the
    # freely chosen name, `None` means "No room". `category` is the id
    # from `profiles.categories.Category` (`socket`, `light`, …), NOT the
    # translated name - the UI sets that itself via
    # `t("web.devices.category." + category)`, so that searching for
    # "Steckdose" or "socket" matches in whichever language is displayed.
    # `category_rank` comes from the same source as the category, instead
    # of maintaining the order a second time in JavaScript.
    room: str | None
    category: str
    category_rank: int


class SignalPatch(BaseModel):
    """What can be changed on a signal at all.

    Spec 6.2: the key is the wiring in Loxone. If it were changeable here,
    a click in the UI could silently kill a component in the house dead -
    which is why this model has no `key` field at all. A `key` sent along
    anyway never lands on the object with Pydantic and is accordingly
    never read by `devices.rename_signal`, let alone applied - that is not
    a matter of care in the handler, but one this model makes structurally
    impossible. The reason is Pydantic v2's own default for unknown
    fields, `extra="ignore"` (correction M1, review 2026-09-02: this
    incorrectly said `extra="allow"` as the default - the opposite, which
    would actually keep unknown fields).
    """

    model_config = ConfigDict(frozen=True)

    title: str | None = None
    exported: bool | None = None
    resend: bool | None = None


class DevicePatch(BaseModel):
    """`PATCH /api/devices/{device_id}` - label and room, nothing else.

    Was called `DeviceRename` up to the device-tab design and could only
    do the label; the name has moved along with the capability. Neither
    `node_id` nor `id` belong here, for the same reason as with
    `SignalPatch`: a route cannot accidentally pick up what the model
    does not know about (Pydantic v2 discards unknown fields via
    `extra="ignore"`).

    `None` means "unchanged" - for BOTH fields, as with `SignalPatch`. The
    room therefore needs a second way to REMOVE it: that is the empty
    string `""`, which `Store.set_room` turns into `NULL` via
    `_normalized_room`. A name made of pure whitespace takes the same
    path - it has the same unambiguous meaning and is therefore not worth
    a 422."""

    model_config = ConfigDict(frozen=True)

    label: str | None = None
    room: str | None = None


class RoomRename(BaseModel):
    """`POST /api/rooms/rename`.

    The fields are named `from_room`/`to_room` internally because `from`
    is a Python keyword; externally they carry the short names that
    appear in the JSON via `alias`. `populate_by_name` allows both, so a
    test can also build the model directly with the Python names."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    from_room: str = Field(alias="from")
    to_room: str = Field(alias="to")


class CommandOut(BaseModel):
    """A control for `GET /api/devices/{device_id}/controls` (Task 4).

    Deliberately carries only what a click needs - the key to trigger it
    and the slug as a label. `takes_value` tells the UI whether a simple
    button is enough (e.g. `on`) or a slider is needed (e.g. `level`)."""

    model_config = ConfigDict(frozen=True)

    key: str
    slug: str
    takes_value: bool


class ControlsOut(BaseModel):
    """Response of `GET /api/devices/{device_id}/controls` (Task 4).

    `hidden_raw_commands` (review fix Minor #4, 2026-09-02): how many of
    the device's commands were filtered out because `profiles.table.
    command_slug` does not know them (see `CommandOut` above and the
    route's docstring). The filter itself remains correct - a button with
    no recognisable meaning would be useless -, but without this number
    an unknown command vanishes without a trace for a person diagnosing a
    foreign device. With it, the UI can say: "N more commands present but
    not named" instead of silently showing nothing."""

    model_config = ConfigDict(frozen=True)

    commands: list[CommandOut]
    hidden_raw_commands: int


class ValueIn(BaseModel):
    """Body of `POST /api/commands/{key}` and `POST /api/signals/{key}/write`
    (Task 4) - the same string value that `/cmd/{key}/{value}` (Phase 4)
    accepts as a path segment. One value, one type, in both places (Spec 4.2)."""

    model_config = ConfigDict(frozen=True)

    value: str


class CommissionRequest(BaseModel):
    """`POST /api/devices/commission` - the pairing code from the device or
    its packaging (11 digits, or the 21-character `MT:` code, Spec 7.1).

    `thread_dataset` is optional: only Thread devices need it, and only
    before `commission_with_code` is even attempted (see
    `BridgeMatterClient.commission_with_code` - without a prior
    `set_thread_dataset` call a Thread device fails with "Required
    network information not provided").
    """

    model_config = ConfigDict(frozen=True)

    code: str
    thread_dataset: str | None = None
    # Room (device-tab design, 2026-09-05, section 6.7): optional, because
    # a device without a chosen room lands under "No room" and can be
    # assigned one at any later time. If commissioning fails, no device is
    # created and therefore no room either.
    room: str | None = None


class ExportDeviceOut(BaseModel):
    """A device in the response of `GET /api/export/preview` (Task 5).

    Mirrors the output of `loxmatter export` on the command line
    (`cli.py`) as numbers instead of terminal lines: `inputs` and
    `commands` are the number of objects that would result in the two
    template files, `skipped` the signals that produce no Loxone input
    (lists, structs, text, null values - Spec 6.6), regardless of the
    `exported` flag. `viu_filename`/`vo_filename` are the same names that
    also end up in the ZIP of `GET /api/export/download` (`filename_for`)
    - so the UI can already show which files will be produced before the
    download.

    `hidden_count` (Task 8): how many of this device's signals the signal
    list hides by default in the collapsed "expert" block, because
    `profiles.relevance.is_functional` does not classify them as intended
    (`StoredSignal.functional`) - regardless of whether they would be
    technically exportable. For the test template's plug, that is 154 of
    159 signals."""

    model_config = ConfigDict(frozen=True)

    device_id: int
    label: str
    viu_filename: str
    vo_filename: str
    inputs: int
    commands: int
    skipped: int
    hidden_count: int


class ExportPreviewOut(BaseModel):
    """Response of `GET /api/export/preview` (Task 5) - a pure preview, no
    write access (see `api.export.preview`)."""

    model_config = ConfigDict(frozen=True)

    devices: list[ExportDeviceOut]
    system_files: list[str]


class ExportStatusOut(BaseModel):
    """A device in the response of `GET /api/export/status` (Task 5).

    `exported_at` is `None` as long as a device has never been exported
    via `GET /api/export/download` (API) or `loxmatter export` (CLI) -
    both write the same timestamp into the same database (see
    `model.store.Store.mark_exported`). `changed_since_export` is also
    `True` in that case: without a previous export there is nothing the
    current state could be unchanged against."""

    model_config = ConfigDict(frozen=True)

    device_id: int
    label: str
    exported_at: str | None
    changed_since_export: bool


class BridgeSettingsOut(BaseModel):
    """Response of `GET`/`PATCH /api/settings` (device dashboard design,
    section 4). `bridge_ip`/`saved_at` are `None` as long as no one has
    set up the connection to the Miniserver - the case in which the UI
    disables the export button on every device card."""

    model_config = ConfigDict(frozen=True)

    bridge_ip: str | None
    udp_port: int
    listen_port: int
    saved_at: str | None


class BridgeSettingsIn(BaseModel):
    """Body of `PATCH /api/settings` - all three fields together, no
    partial update: they belong together functionally (the same virtual
    connection), a partial update could otherwise leave a valid IP paired
    with a now-wrong port. `min_length=1` on `bridge_ip` yields 422 for an
    empty field, without a dedicated validator."""

    model_config = ConfigDict(frozen=True)

    bridge_ip: str = Field(min_length=1)
    udp_port: int
    listen_port: int


class ProjectSyncEntryOut(BaseModel):
    """A row in the diff plan of `POST /api/export/project-sync` (design
    section 5/7). `changes` is always empty outside of
    `status == "updated"`."""

    model_config = ConfigDict(frozen=True)

    kind: str
    device_id: int
    device_label: str
    key: str
    title: str
    status: str
    changes: dict[str, list[str]]


class ProjectSyncMiniserverOut(BaseModel):
    """A Miniserver found in the uploaded project file
    (`projectsync.index.MiniserverCandidate`) - fills the selection field
    in the WebUI when the file configures more than one (user request:
    select the desired Miniserver instead of typing its IP by hand)."""

    model_config = ConfigDict(frozen=True)

    title: str
    int_addr: str


class ProjectSyncPlanOut(BaseModel):
    """Response of `POST /api/export/project-sync` - plan and both patched
    file variants in one response (design section 4/7): no second server
    round trip, the "confirm" step is purely client-side.

    **Two response shapes** (user request after the review): if the file
    carries more than one Miniserver and none was selected, the endpoint
    returns, instead of a plan, `needs_miniserver_selection=True` plus
    `available_miniservers` - all other fields then stay empty/`None`.
    The WebUI shows a selection field instead of the plan in this case
    and requests again with the same file (already present in the
    browser, no repeated file dialog) and the chosen `miniserver_ip`."""

    model_config = ConfigDict(frozen=True)

    needs_miniserver_selection: bool = False
    available_miniservers: list[ProjectSyncMiniserverOut] = Field(default_factory=list)
    entries: list[ProjectSyncEntryOut] = Field(default_factory=list)
    has_changes: bool = False
    patched_conservative_base64: str | None = None
    # `None` if the experimental variant could not be built for this file
    # (e.g. missing `VirtualInCaption` section, design section 8). The
    # plan and the conservative variant remain unaffected by that - the
    # UI then shows only the reason instead of the download offer.
    patched_with_new_devices_base64: str | None = None
    new_devices_unavailable_reason: str | None = None


class ResendIntervalOut(BaseModel):
    """Response of `GET`/`PATCH /api/settings/resend-interval` (periodic
    resend design, 2026-09-04, section 5)."""

    model_config = ConfigDict(frozen=True)

    interval_seconds: float


class ResendIntervalIn(BaseModel):
    """Body of `PATCH /api/settings/resend-interval`. `gt=0` catches a
    non-positive value here already (422 without a dedicated validator);
    the actual lower bound (`MIN_RESEND_INTERVAL_SECONDS`) is checked by
    `ResendSettingsStore.set_interval_seconds` itself, see there."""

    model_config = ConfigDict(frozen=True)

    interval_seconds: float = Field(gt=0)

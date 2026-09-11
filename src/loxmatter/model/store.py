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

"""SQLite storage for devices and signals.

A signal's key is the wiring in Loxone (Spec 6.2). It is assigned once and
never changed afterward - neither on renaming nor on re-reading the same
device. That is why it lives in a database rather than being derived at
runtime.

device_id is never reused: a removed and newly commissioned device gets new
keys, so it does not silently inherit old wiring.

Key format (Spec 6.2): ``d<device_id>_<endpoint>_<slug>``, e.g.
``d12_1_temp``. Two signals on the same endpoint can carry the same
profile slug (e.g. several events of the same cluster that happen to be
named the same, or a generic slug for two unknown attributes) - in that
case ``_assign_key`` appends the element ID (``d12_1_temp_5``) to satisfy
the uniqueness enforced by the ``signal.key`` table without changing the
key of a signal that has already been assigned one.

A store instance belongs to exactly one thread and exactly one event loop -
without `check_same_thread=False`, `sqlite3.Connection` is bound to its
creating thread, and this module deliberately does not deviate from that.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from loxmatter import i18n
from loxmatter.export.commands import DeviceCommand, extract_commands
from loxmatter.matter.discovery import extract_signals
from loxmatter.matter.models import NodeSnapshot, SignalKind, SignalRef
from loxmatter.model.auth_store import AuthStore
from loxmatter.model.locale_store import LocaleStore
from loxmatter.model.resend_settings_store import ResendSettingsStore
from loxmatter.model.settings_store import BridgeSettingsStore
from loxmatter.model.update_settings_store import UpdateSettingsStore
from loxmatter.profiles.categories import category_for
from loxmatter.profiles.relevance import (
    ROOT_NODE_DEVICE_TYPE,
    UTILITY_ENDPOINT_KEEP_CLUSTERS,
    device_types_by_endpoint,
    is_functional,
)
from loxmatter.profiles.table import (
    Exportability,
    element_rank_for,
    is_exportable,
    lookup,
    rank_for,
    struct_field,
)
from loxmatter.timestamps import now_iso

DEFAULT_UDP_PORT = 7000
# `_DEFAULT_LISTEN_PORT` lifted here from `api/export.py` (device dashboard
# design, section 4): the new `BridgeSettingsStore` below needs the same
# default value, and a second, independently maintained literal `8080`
# would be exactly the kind of drift `api/export.py`'s own module docstring
# (decision 2) already warns against.
DEFAULT_LISTEN_PORT = 8080

# Schema version of this module, managed via `PRAGMA user_version` (review
# fix Important #1, 2026-09-02). `CREATE TABLE IF NOT EXISTS` alone never
# reaches an already existing table with a new column - a database created
# before the `exported` field permanently lacked this column until a
# migration was added, and `Store.signals()` failed with `IndexError`.
# Version 0 is "before this migration logic" (every existing database,
# `PRAGMA user_version` never set); version 1 adds `signal.exported` and
# backfills existing rows retroactively, see `_migrate_to_v1`. Version 2
# (Task 5, Phase 5) adds `device.exported_at` and `device.updated_at`, see
# `_migrate_to_v2`. Version 3 (Task 7, Phase 6) adds no column - it
# re-derives `signal.title`, `signal.unit` and the default value of
# `signal.exported` for EXISTING rows from the profile table, see
# `_migrate_to_v3`. Version 4 (Task 8, Phase 6) adds `signal.functional`,
# see `_migrate_to_v4`. Version 5 (WebUI login) adds the tables `setting`
# and `session`, see `_migrate_to_v5` - both are already present in a fresh
# database via `_SCHEMA`, so the migration is only needed for existing
# databases. Version 6 (periodic resend design, 2026-09-04) adds
# `signal.resend`, see `_migrate_to_v6` - no backfill, every existing row
# starts at the column default (0/off). Version 7 (device tab design,
# 2026-09-05) adds `device.room` and `device.device_types`, see
# `_migrate_to_v7` - no backfill for either, but for two different reasons:
# `room = NULL` IS the correct meaning ("no room"), while
# `device_types = NULL` only means "not yet backfilled" and gets filled from
# the snapshots that are fetched anyway on the next bridge start
# (`backfill_device_types`). A migration cannot do that: it only ever sees
# the database, never a `NodeSnapshot`.
#
# **Why the login move gets 5, not 4.** Both efforts arose in parallel and
# each claimed 4. A database that has already seen Phase 6 is at 4 - a
# second migration under the same number would be silently skipped by
# `_migrate`, and the service would start without the tables it needs to
# sign in. The number depends on the order in which the changes were
# merged, not on when they were written.
# Version 8 (device groups, design 2026-09-10) adds the three tables
# `device_group`, `device_group_member` and `group_command`, see
# `_migrate_to_v8` - all three are already present in a fresh database via
# `_SCHEMA`, so the migration is only needed for existing databases.
_SCHEMA_VERSION = 8


def schema_version() -> int:
    """The schema version of this module, publicly readable.

    `_SCHEMA_VERSION` remains private: whoever changes it should see the long
    comment block above it, which justifies each step.
    This function exposes it outward so that `loxmatter.version` and the
    CI don't need to access a private name - and so there is
    exactly ONE source for this number.
    """
    return _SCHEMA_VERSION


_SCHEMA = """
CREATE TABLE IF NOT EXISTS device (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    unique_id    TEXT NOT NULL,
    node_id      INTEGER NOT NULL,
    label        TEXT NOT NULL,
    udp_port     INTEGER NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1,
    exported_at  TEXT,
    updated_at   TEXT,
    room         TEXT,
    device_types TEXT
);
CREATE TABLE IF NOT EXISTS signal (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id     INTEGER NOT NULL REFERENCES device(id),
    endpoint      INTEGER NOT NULL,
    cluster_id    INTEGER NOT NULL,
    element_id    INTEGER NOT NULL,
    kind          TEXT NOT NULL,
    key           TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL,
    unit          TEXT NOT NULL,
    exportability TEXT NOT NULL,
    exported      INTEGER NOT NULL DEFAULT 1,
    functional    INTEGER NOT NULL DEFAULT 1,
    resend        INTEGER NOT NULL DEFAULT 0,
    UNIQUE (device_id, endpoint, cluster_id, element_id, kind)
);
CREATE TABLE IF NOT EXISTS command (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   INTEGER NOT NULL REFERENCES device(id),
    node_id     INTEGER NOT NULL,
    endpoint    INTEGER NOT NULL,
    cluster_id  INTEGER NOT NULL,
    command_id  INTEGER NOT NULL,
    key         TEXT NOT NULL UNIQUE,
    slug        TEXT NOT NULL,
    takes_value INTEGER NOT NULL,
    UNIQUE (device_id, endpoint, cluster_id, command_id)
);
CREATE TABLE IF NOT EXISTS setting (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session (
    id         TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS device_group (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    label       TEXT NOT NULL,
    room        TEXT,
    category    TEXT NOT NULL,
    exported_at TEXT,
    updated_at  TEXT
);
CREATE TABLE IF NOT EXISTS device_group_member (
    group_id  INTEGER NOT NULL REFERENCES device_group(id),
    device_id INTEGER NOT NULL REFERENCES device(id),
    UNIQUE (group_id, device_id)
);
CREATE TABLE IF NOT EXISTS group_command (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id    INTEGER NOT NULL REFERENCES device_group(id),
    cluster_id  INTEGER NOT NULL,
    command_id  INTEGER NOT NULL,
    key         TEXT NOT NULL UNIQUE,
    slug        TEXT NOT NULL,
    takes_value INTEGER NOT NULL,
    UNIQUE (group_id, cluster_id, command_id)
);
"""


def _add_column_if_missing(db: sqlite3.Connection, table: str, column: str, ddl: str) -> bool:
    """Adds `column` to `table` if it is missing - shared safeguard for
    `_migrate_to_v1` and `_migrate_to_v2` (review fix Minor #3, 2026-09-02:
    both check `PRAGMA table_info`, the same pitfall, the same idea,
    previously written out by hand twice instead of shared once).

    The pitfall the column check guards against: a freshly created database
    already has a new column via `_SCHEMA`'s `CREATE TABLE IF NOT EXISTS`,
    while its `PRAGMA user_version` is also still at 0 (see `_migrate`).
    `ALTER TABLE ... ADD COLUMN` would then run against an already existing
    column and fail with "duplicate column".

    Returns whether the column was newly added (`False` if it was already
    there) - `_migrate_to_v1` needs this to run its backfill only for a
    genuine legacy database, not for a fresh one."""
    columns = {str(row["name"]) for row in db.execute(f"PRAGMA table_info({table})")}
    if column in columns:
        return False
    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    return True


def _migrate_to_v1(db: sqlite3.Connection) -> None:
    """Adds `signal.exported` and backfills existing rows based on their
    `exportability` (review fix Important #1 and #2, 2026-09-02) - the same
    rule as for a freshly registered signal, see
    `profiles.table.is_exportable`.

    The backfill only runs if `_add_column_if_missing` actually added the
    column - for a freshly created database (column already present via
    `_SCHEMA`) there are no existing rows that would need retroactive
    filling.
    """
    if not _add_column_if_missing(db, "signal", "exported", "INTEGER NOT NULL DEFAULT 1"):
        return
    # Derived from `is_exportable` instead of enumerated by hand here a
    # third time (review fix Fix 8, 2026-09-03, together with the two
    # copies in `cli.py` and `api/export.py`): an SQL query needs the
    # values as a list, not the function - but the list itself now still
    # comes from that one single source.
    exportable_values = tuple(e.value for e in Exportability if is_exportable(e))
    placeholders = ", ".join("?" for _ in exportable_values)
    db.execute(
        f"UPDATE signal SET exported = CASE WHEN exportability IN ({placeholders})"
        " THEN 1 ELSE 0 END",
        exportable_values,
    )


def _migrate_to_v2(db: sqlite3.Connection) -> None:
    """Adds `device.exported_at` and `device.updated_at` (Task 5, Phase 5) -
    the basis for `GET /api/export/status`: when a device was last exported,
    and whether anything has changed since then.

    Both columns stay NULL on an already existing row instead of being
    backfilled retroactively - unlike `_migrate_to_v1` there is no existing
    value here from which a meaningful point in time could be derived.
    `NULL` means "never exported" for `exported_at` (the same meaning as for
    a freshly registered device) and "unknown" for `updated_at` -
    `api.export._status_for` treats an unknown `updated_at` as "changed
    since then", the more cautious of the two possible assumptions.

    Each column checked individually via `_add_column_if_missing`, because a
    database that sits at exactly version 1 (`signal.exported` present,
    neither column here yet) need not be distinguishable from a genuine
    legacy database (version 0, runs `_migrate_to_v1` and `_migrate_to_v2`
    one after the other in the same run) - both end up here with missing
    columns and get them added."""
    _add_column_if_missing(db, "device", "exported_at", "TEXT")
    _add_column_if_missing(db, "device", "updated_at", "TEXT")


def _endpoint0_device_types(rows: Sequence[sqlite3.Row]) -> dict[int, dict[int, frozenset[int]]]:
    """Fallback rule for `profiles.relevance.device_types_by_endpoint` when
    no device snapshot is available, only already stored rows - shared
    basis for `_migrate_to_v3` (Task 7) and `_migrate_to_v4` (Task 8): both
    have to call `is_functional` without a `NodeSnapshot`, for exactly the
    same reason (see the docstring section "Where the device types per
    endpoint come from" below under `_migrate_to_v3`) and with exactly the
    same fallback rule - a second copy that differs only slightly would not
    be justifiable for two migrations that ask the same question.

    Endpoint 0 always counts as the root node (Matter core specification
    9.2.1), and PowerSource additionally as soon as endpoint 0 carries any
    signal of that cluster at all (`relevance.UTILITY_ENDPOINT_KEEP_CLUSTERS`,
    so far the only case covered). `rows` must carry at least the columns
    `device_id`, `endpoint` and `cluster_id`."""
    clusters_on_endpoint0: dict[int, set[int]] = {}
    for row in rows:
        if int(row["endpoint"]) == 0:
            clusters_on_endpoint0.setdefault(int(row["device_id"]), set()).add(
                int(row["cluster_id"])
            )

    device_types_by_device: dict[int, dict[int, frozenset[int]]] = {}
    for device_id, endpoint0_clusters in clusters_on_endpoint0.items():
        declared = {ROOT_NODE_DEVICE_TYPE}
        for cluster_id, required_type in UTILITY_ENDPOINT_KEEP_CLUSTERS.items():
            if cluster_id in endpoint0_clusters:
                declared.add(required_type)
        device_types_by_device[device_id] = {0: frozenset(declared)}
    return device_types_by_device


def _migrate_to_v3(db: sqlite3.Connection) -> None:
    """Re-derives `title`, `unit` and the default value of `exported` for
    EXISTING signals (Task 7) - the key itself stays untouched in every
    case, see the module docstring and main document 6.2.

    **Why retroactive, not only for newly commissioned devices:** Task 6
    already wired up `profiles.relevance.is_functional`, but only in
    `register_signals` - a device commissioned yesterday would never see
    the correction unless it is fully re-commissioned. Two rule sets whose
    difference depends solely on the commissioning date would be
    impossible to explain to anyone.

    **The key stays untouched.** This migration never writes to the `key`
    column. Consequence: a device commissioned before this update keeps
    e.g. `d2_0_c47_a12` and is now called "battery"; a device commissioned
    afterward gets the new key `d2_0_battery` for the same value. Two keys
    for the same value, depending on the commissioning date - ugly, but
    deliberate (main document 6.2): the alternative would be a silently
    dead functional block in someone else's Loxone configuration.

    **Where the device types per endpoint come from (the decision
    deliberately left open in the task scope):** `is_functional` needs the
    device types declared by the device per endpoint to distinguish a
    management endpoint (root node, OTA requestor) from a functional
    endpoint - this information lives in the device snapshot (descriptor
    cluster), not in this database.

    A new column that `register_device`/`register_signals` write from now
    on does NOT solve this: a migration runs when a database is opened
    (`_migrate` calls it with `db: sqlite3.Connection`, never with a
    `NodeSnapshot`) and therefore NEVER has a snapshot at hand - not even
    in a future migration. And the existing rows to be migrated here were
    created before such a column existed, so they would have nothing to
    fill it with anyway. A new column would thus be worthless for THIS
    migration; it could only have helped if it had already existed at the
    time of the original registration.

    Hence the second path from the task scope: a fallback rule derived from
    the cluster/endpoint numbers already stored. Matter structurally
    guarantees that endpoint 0 is always the root node endpoint (core
    specification 9.2.1) - that is the only statement about a management
    endpoint that can be made reliably WITHOUT a snapshot; every other
    endpoint counts here as an ordinary functional endpoint. For the only
    exception case covered so far
    (`relevance.UTILITY_ENDPOINT_KEEP_CLUSTERS`: PowerSource, cluster 47),
    the associated functional device type counts as declared as soon as
    endpoint 0 carries any signal of that cluster at all - a device only
    exposes the PowerSource cluster on its root endpoint if it actually has
    a battery level to report there. Cross-checked against both checked-in
    snapshots (`tests/fixtures/nodes/`, see `test_store_migration.py`,
    `test_the_migration_reproduces_the_functional_export_counts_of_both_fixtures`):
    this fallback rule produces, for the plug and the button, exactly the
    same count of exported signals as a fresh registration with a real
    snapshot - 5 and 17 respectively.

    **`exportability` is only raised in a single, tightly scoped case,
    otherwise left untouched:** `classify()` (Spec 6.6) fundamentally needs
    a real runtime value, which a migration never has - the stored value
    therefore remains, as a rule, the best truth available. The one
    exception: if the table entry today carries a field number
    (`profiles.table.struct_field` - Task 5, the counter reading), the
    element derived from it counts as mappable (ANALOG), REGARDLESS of the
    stored value. Rationale: only someone who has checked the Matter
    specification text that this exact struct element is numeric enters a
    field number (cluster-author knowledge, not a runtime property) - a
    row whose structure was not yet readable at registration time (no
    `field:` in `clusters.yaml` at the time, hence `exportability=none`,
    Spec 6.6) is raised to ANALOG by this migration exactly as it would be
    by a fresh re-read with a real value. For every named entry WITHOUT a
    field number, `classify(struct_member(ref, value))` is identical to
    `classify(value)` anyway - naming alone never changes the
    classification, only a new field number does.

    **Two open limits of this exception, deliberately accepted rather than
    eliminated (Task 7, follow-up fix 1):**
    - It does not see the runtime value: a counter that has NEVER been
      measured (Matter returns `null` for that, not a numeric value) is
      still raised to ANALOG and then counts as exported - a Loxone input
      that never carries a value, a checkbox in the UI that lies. Nothing
      wrong happens at runtime, though: `to_loxone_value` derives
      independently from the real value and still returns `None` for it
      (Spec 6.6), so a made-up value never flows in - only the generated
      template gets one input too many. Dropping the exception entirely
      for this reason was considered and rejected: the plug
      (`tests/fixtures/nodes/ikea_grillplats_plug.json`) already reports a
      real value (0, not `null`) for `energy_imported` (cluster 145,
      element 1) - a fresh registration classifies this signal as ANALOG
      and therefore exported via exactly this exception (see `lookup`).
      Without the exception the migrated exportability would stay at the
      pre-Task-6 value NONE (see `_pretend_unnamed_profile` in
      `test_store_migration.py`), and the cross-check
      (`test_the_migration_reproduces_the_functional_export_counts_of_both_fixtures`)
      would drop for the plug from 5 to 4 - precisely the number this
      module itself cites as evidence for the fallback rule. Dropping it
      would therefore trade a provably correct behaviour (the plug) for a
      purely hypothetical one, unobservable on either of the two checked-in
      snapshots (the never-measured counter) - the exception therefore
      stays, with this limit named openly here rather than accepted
      silently. See
      `test_a_never_measured_energy_counter_is_still_promoted_by_the_field_number_exception`.
    - It is hard-wired to ANALOG, not to the actual struct semantics: both
      cases known today (`energy_imported`/`energy_exported`,
      `EnergyMeasurementStruct.energy`) are numeric per the Matter
      specification - so ANALOG is correct in every case today. If
      `clusters.yaml` ever enters a `field:` on a boolean struct element,
      this line would be wrong (DIGITAL would be correct): the table does
      not yet know a type per field, only the field number itself.
      Unaddressed because no such case exists yet - whoever creates the
      first case of this kind must think this spot through.

    **What this migration otherwise CANNOT do:**
    - Beyond the field-number exception above, `exportability` is NOT
      reclassified. A fix in `clusters.yaml` that would change a value's
      classification for some other reason therefore still only reaches an
      already stored signal on the next genuine re-read of the device
      (`register_signals`), not through this migration. `title` and `unit`
      are unaffected by this: neither ever depends on the runtime value in
      `profiles.table.lookup`.
    - A title personalised by the user via `set_title` is also overwritten
      by this one-time migration - the database does not distinguish
      whether a stored title is the automatic default or a deliberate
      rename.
    - The same applies to `exported`: per `register_signals`/`set_exported`,
      the value belongs to the user from the moment a signal first becomes
      known - a deliberately toggled signal stays untouched on every
      subsequent re-read (see there). This migration cannot honour that: it
      rewrites `exported` once for EVERY row, because the schema has no
      column that distinguishes "set by the user" from "automatic default"
      - unavoidable within the task's scope. An operator who has manually
      enabled (or disabled) twelve signals loses that selection with this
      one update. Reassurance: the runtime path (`loxone.runtime`) does NOT
      filter on `exported` when sending - an existing UDP wiring does not
      die because of this. Only a NEWLY generated Loxone template after
      this update is affected (`export.signals.to_inputs` filters on
      `exported` there, see its docstring).
    - A management endpoint beyond endpoint 0 (not ruled out from Matter's
      point of view, but never observed on either of the two real devices
      known so far) is not recognised by the fallback rule; such a device
      would remain exported more generously after the migration than a
      genuine fresh registration would. But that is NOT the only deviation
      from `device_types_by_endpoint`, and not always the more generous
      direction - "at most more generous" would be a false guarantee here,
      see the following two points.
    - The fallback rule infers from "cluster 47 (PowerSource) sits on
      endpoint 0" that "PowerSource is declared there as a device type" -
      but `is_functional` asks about the device-type declaration, not mere
      cluster presence. For a button whose descriptor on endpoint 0
      actually names only root node (the PowerSource cluster is present but
      not reported there as a device type): a fresh registration exports 16
      signals, this migration 17 - in this case the migration IS more
      generous.
    - If a snapshot reports no `DeviceTypeList` at all for endpoint 0,
      `is_functional` treats endpoint 0 on a fresh registration like an
      ordinary FUNCTIONAL endpoint
      (`profiles.relevance.device_types_by_endpoint`: an endpoint without a
      descriptor entry does not appear there at all, and a missing
      declaration thereby excludes layer 2 in `is_functional`). This
      migration, by contrast, ALWAYS assumes endpoint 0 = root node,
      regardless of whether any snapshot ever confirmed that. Example:
      freshly 108 exported signals, migrated 17 - here the migration is
      markedly LESS generous, the opposite direction of the two points
      above.
    - If the re-derivation fails for a single row (e.g. an unexpected value
      in `kind`), EXACTLY THAT row is left unchanged - no abort of the
      entire migration, no half-migrated database (design 8, see
      `test_an_unparseable_row_survives_the_migration_untouched`).
    """
    rows = db.execute(
        "SELECT device_id, key, endpoint, cluster_id, element_id, kind, exportability FROM signal"
    ).fetchall()
    device_types_by_device = _endpoint0_device_types(rows)

    for row in rows:
        try:
            ref = SignalRef(
                int(row["endpoint"]),
                int(row["cluster_id"]),
                int(row["element_id"]),
                SignalKind(row["kind"]),
            )
            # `value=None`: `lookup` only needs the runtime value for the
            # exportability classification - `title` and `unit` never
            # depend on `value` in `profiles.table.lookup` (see docstring
            # above).
            profile = lookup(ref, None)
            # No entry needed if this device has no signal at all on
            # endpoint 0 - `is_functional` treats a missing endpoint there
            # like an ordinary functional endpoint anyway.
            device_types = device_types_by_device.get(int(row["device_id"]), {})
            exportability = Exportability(row["exportability"])
            if struct_field(ref) is not None:
                # The only exception where this migration raises a
                # classification WITHOUT a runtime value (see docstring,
                # "field number").
                exportability = Exportability.ANALOG
            exported = int(is_exportable(exportability) and is_functional(ref, device_types))
        except (ValueError, KeyError):
            # This one row stays unchanged (see docstring, "What this
            # migration otherwise CANNOT do") - no abort of the
            # transaction.
            continue
        db.execute(
            "UPDATE signal SET title = ?, unit = ?, exportability = ?, exported = ? WHERE key = ?",
            (profile.title, profile.unit, exportability.value, exported, row["key"]),
        )


def _migrate_to_v4(db: sqlite3.Connection) -> None:
    """Adds `signal.functional` and backfills existing rows (Task 8).

    **Why a separate column even though `exported` already exists.** Both
    start at the same value when a signal is created (`register_signals`) -
    `is_exportable(...) and is_functional(...)` - but then diverge as soon
    as someone flips a checkbox in the signals view (`PATCH
    /api/signals/{key}`, `set_exported`): `exported` then belongs to the
    user, while `is_functional` remains a pure property of the device that
    does not change through a click. The UI, however, needs exactly this
    second answer, UNAFFECTED by the user, to organise the signal list into
    "functional" and "expert" (`api.devices._signal_out`) - a user who
    manually exports a technical signal should not thereby also lift it out
    of the expert block, and vice versa. Without a dedicated column there
    would only be two bad alternatives: recompute `is_functional` on every
    request (needs the device types per endpoint, see `_migrate_to_v3`'s
    docstring - exactly the problem that already argued against a column
    there, but no longer applies NOW, because `register_signals` already
    knows the result from now on anyway and can write it along), or abuse
    `exported` for both questions at once and thereby silently lose the
    user's selection on toggling.

    **The fallback case (existing rows without a device snapshot) goes
    through the same fallback rule as `_migrate_to_v3`**, see
    `_endpoint0_device_types` - the same limits documented there apply here
    unchanged (endpoint 0 = root node assumed, PowerSource recognised only
    via cluster presence rather than a genuine device-type declaration, no
    knowledge of a management endpoint beyond endpoint 0). Unlike
    `_migrate_to_v3`, this migration does not touch `exportability` or
    `exported` - only the new column.

    Like `_migrate_to_v1`: the backfill only runs if the column was
    actually newly added, not for a freshly created database (column
    already present via `_SCHEMA`, no existing rows). A row that cannot be
    parsed is left at the column default `functional = 1` - the same
    conservative failure case as in `_migrate_to_v3` ("this one row stays
    unchanged" there means for `exported`, here for `functional`: when in
    doubt, visible rather than hidden)."""
    if not _add_column_if_missing(db, "signal", "functional", "INTEGER NOT NULL DEFAULT 1"):
        return
    rows = db.execute(
        "SELECT device_id, key, endpoint, cluster_id, element_id, kind FROM signal"
    ).fetchall()
    device_types_by_device = _endpoint0_device_types(rows)

    for row in rows:
        try:
            ref = SignalRef(
                int(row["endpoint"]),
                int(row["cluster_id"]),
                int(row["element_id"]),
                SignalKind(row["kind"]),
            )
            device_types = device_types_by_device.get(int(row["device_id"]), {})
            functional = int(is_functional(ref, device_types))
        except (ValueError, KeyError):
            # This one row is left at the column default (see docstring) -
            # no abort of the entire migration.
            continue
        db.execute("UPDATE signal SET functional = ? WHERE key = ?", (functional, row["key"]))


def _migrate_to_v5(db: sqlite3.Connection) -> None:
    """Creates `setting` and `session` (WebUI login).

    `CREATE TABLE IF NOT EXISTS` and not `CREATE TABLE`: a freshly created
    database already has both tables via `_SCHEMA`, but is also at `PRAGMA
    user_version = 0` and therefore runs through the same migration chain
    (see `_migrate` and `_add_column_if_missing` for the same pitfall with
    columns).

    No backfill: an existing database has no password and no session, and
    that is exactly the right state - it goes through initial setup after
    the update (Spec 5)."""
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS setting (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS session (
            id         TEXT PRIMARY KEY,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        );
        """
    )


def _migrate_to_v6(db: sqlite3.Connection) -> None:
    """Adds `signal.resend` (periodic resend as opt-in, design 2026-09-04) -
    no backfill: every existing row starts at `resend = 0`, exactly the
    column default. Unlike `exported` (`_migrate_to_v1`), there is no
    existing value here from which a meaningful default could be derived -
    on the contrary, "off" is explicitly the desired default here (see
    design, section 3): the periodic full resend should only start again
    for EVERY signal after this update through a deliberate user
    decision."""
    _add_column_if_missing(db, "signal", "resend", "INTEGER NOT NULL DEFAULT 0")


def _migrate_to_v7(db: sqlite3.Connection) -> None:
    """Adds `device.room` and `device.device_types` (device tab design,
    2026-09-05, section 3.1).

    Two columns in one step, like `_migrate_to_v2` - both belong to the
    same effort and would never occur individually.

    No backfill. For `room` there is no existing value from which a room
    could be derived, and `NULL` is the intended meaning anyway ("no
    room"). For `device_types` there would be one - the Matter device
    types live in the `NodeSnapshot` - but a migration does not have
    exactly that available: it receives an `sqlite3.Connection` and
    nothing else. `Store.backfill_device_types` takes over the backfilling
    when the bridge starts, where the snapshots are fetched anyway."""
    _add_column_if_missing(db, "device", "room", "TEXT")
    _add_column_if_missing(db, "device", "device_types", "TEXT")


def _migrate_to_v8(db: sqlite3.Connection) -> None:
    """Adds the three group tables (design 2026-09-10, section 4.2).

    `CREATE TABLE IF NOT EXISTS` and not `CREATE TABLE`, for the same
    reason as in `_migrate_to_v5`: a freshly created database already has
    all three via `_SCHEMA` and is nevertheless at `PRAGMA user_version =
    0`, so it runs through this migration too. No backfill - no existing
    database has groups.
    """
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS device_group (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            label       TEXT NOT NULL,
            room        TEXT,
            category    TEXT NOT NULL,
            exported_at TEXT,
            updated_at  TEXT
        );
        CREATE TABLE IF NOT EXISTS device_group_member (
            group_id  INTEGER NOT NULL REFERENCES device_group(id),
            device_id INTEGER NOT NULL REFERENCES device(id),
            UNIQUE (group_id, device_id)
        );
        CREATE TABLE IF NOT EXISTS group_command (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id    INTEGER NOT NULL REFERENCES device_group(id),
            cluster_id  INTEGER NOT NULL,
            command_id  INTEGER NOT NULL,
            key         TEXT NOT NULL UNIQUE,
            slug        TEXT NOT NULL,
            takes_value INTEGER NOT NULL,
            UNIQUE (group_id, cluster_id, command_id)
        );
        """
    )


# Migrations in order, applied from whichever version is stored - to extend
# for a later schema change: simply append, with the next version number as
# the key.
_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: _migrate_to_v1,
    2: _migrate_to_v2,
    3: _migrate_to_v3,
    4: _migrate_to_v4,
    5: _migrate_to_v5,
    6: _migrate_to_v6,
    7: _migrate_to_v7,
    8: _migrate_to_v8,
}


def _migrate(db: sqlite3.Connection) -> None:
    """Brings an opened database up to `_SCHEMA_VERSION`.

    Runs in one transaction: if a migration fails, the database stays
    unchanged (review fix Important #1) - `ALTER TABLE ADD COLUMN` is fully
    transactional in SQLite, so an explicit rollback undoes the steps of
    this run that already executed. `PRAGMA user_version` itself is also
    part of this transaction and is therefore only set to `_SCHEMA_VERSION`
    on complete success - a crash in the middle of a migration therefore
    does not leave a half-applied change under an already-raised version
    that a later start would incorrectly consider done.

    Already at the latest state (the normal case on every start except the
    very first after a schema change): no write access, a genuine no-op.
    """
    version = int(db.execute("PRAGMA user_version").fetchone()[0])
    if version >= _SCHEMA_VERSION:
        return
    db.execute("BEGIN")
    try:
        for target_version in range(version + 1, _SCHEMA_VERSION + 1):
            _MIGRATIONS[target_version](db)
        db.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
    except BaseException:
        db.rollback()
        raise
    else:
        db.commit()


@dataclass(frozen=True)
class StoredSignal:
    key: str
    ref: SignalRef
    title: str
    unit: str
    exportability: Exportability
    # Both fields below are already columns of the `signal` table - not a
    # break of key opacity from Spec 6.2 (the key itself stays untouched),
    # only their exposure in the dataclass.
    #
    # device_id (Task 2, Phase 5): the device API resolves a signal via
    # `signal_by_key` WITHOUT device context in the path (`PATCH
    # /api/signals/{key}`) and still needs the associated device_id, e.g.
    # to look up a live value. Parsing the device_id out of the key string
    # would be a break of "keys are opaque" (Spec 6.2) through the back
    # door - store.py already knows the device_id from the row anyway, it
    # just needs to be passed along.
    device_id: int
    # exported (Spec 5, data model): whether this signal should flow into
    # the next export - toggleable by the user (`PATCH
    # /api/signals/{key}`), independent of `exportability`. A signal that
    # is not technically mappable (see Spec 6.6) never has an editable
    # checkbox here, see `exportable` in `api.models.SignalOut`.
    exported: bool
    # functional (Task 8, Phase 6): whether `profiles.relevance.is_functional`
    # classifies this signal as intended for this DEVICE TYPE - unlike
    # `exported`, NOT toggleable by the user and therefore stays unchanged
    # even when a user flips `exported` via checkbox. Both fields start at
    # the same value when created, but diverge from the first click onward
    # - see `_migrate_to_v4`, where this distinction is explained at
    # length. The UI uses only THIS field to organise the signal list into
    # "functional" and "expert" (`api.devices._signal_out`) - there is
    # deliberately no second computation of the rule in the API layer or
    # even in JavaScript.
    functional: bool
    # resend (periodic resend design, 2026-09-04): whether this signal
    # should be resent by the periodic timer even if it has not changed -
    # toggleable by the user (`PATCH /api/signals/{key}`), independent of
    # `exported`/`functional`. Affects ONLY `Runtime.resend_marked()` (the
    # periodic timer); `resend_all()` (for `/resync` and the bridge start)
    # deliberately ignores this field and still sends every known value,
    # see its docstring.
    resend: bool


@dataclass(frozen=True)
class StoredCommand:
    key: str
    slug: str
    node_id: int
    endpoint: int
    cluster_id: int
    command_id: int
    takes_value: bool
    # device_id (review fix Important #1, 2026-09-02): the same rationale
    # as for `StoredSignal.device_id` above - `resolve_command` resolves a
    # command key WITHOUT device context in the path (`POST
    # /api/commands/{key}`), and the calling route still needs the
    # device_id to check whether the associated device is still active.
    device_id: int


@dataclass(frozen=True)
class StoredGroupCommand:
    """A row from `group_command` (design 2026-09-10, section 4.1).

    Carries NO endpoint, unlike `StoredCommand`. Each member has its own,
    and two lamps of different make can hold the same cluster on
    different endpoints - the endpoint therefore belongs to the member and
    is looked up per member at dispatch time (`Store.group_targets`).
    """

    key: str
    slug: str
    group_id: int
    cluster_id: int
    command_id: int
    takes_value: bool


@dataclass(frozen=True)
class GroupTarget:
    """One member of a group, with the command rows that carry out one
    group command on it (design 2026-09-10, section 3).

    `device_label` travels along because the 502 detail names the members
    that failed, and the dispatcher must not have to reach back into the
    store to find out who it was talking to.
    """

    device_id: int
    device_label: str
    commands: tuple[StoredCommand, ...]


def _encode_device_types(types: Mapping[int, frozenset[int]]) -> str:
    """The output of `relevance.device_types_by_endpoint` as JSON for the
    `device.device_types` column.

    Endpoints become strings because JSON has no integer keys; the IDs are
    stored sorted, so that two identical snapshots also produce the same
    text - that makes a comparison in a test readable and prevents a
    meaningless order change from looking like an actual change."""
    return json.dumps({str(endpoint): sorted(ids) for endpoint, ids in sorted(types.items())})


def _decode_device_types(raw: str | None) -> dict[int, frozenset[int]] | None:
    """Counterpart to `_encode_device_types`. `None` means "not yet
    backfilled" (see `_migrate_to_v7`).

    Unreadable JSON also becomes `None` instead of an exception: a row
    manually tampered with must not make the entire device list unusable:
    the device then ends up in the "other" category and is refilled on the
    next bridge start - the same handling as a row that was never filled.
    "Unreadable" here means not just broken JSON, but also syntactically
    valid JSON in the wrong shape - a non-numeric endpoint or type key
    (`ValueError` from `int(...)`) or a non-iterable type list
    (`TypeError`): `_as_device` calls this function for every row, and a
    single hand-tampered row must not take the rest down with it."""
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return None
        return {int(endpoint): frozenset(int(i) for i in ids) for endpoint, ids in parsed.items()}
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _signal_order(signal: StoredSignal) -> tuple[int, int, int, int, int, str]:
    """The sort key of the signal list (design 2026-09-07, section 4,
    with the element rank added later on 2026-09-08).

    Two rank levels, and their position in the tuple is the whole
    statement: the CLUSTER rank comes first and orders the clusters
    against each other (which is why PowerSource falls behind everything
    functional); the ELEMENT rank comes after `cluster_id` and only orders
    within the same cluster (which is why `positions` falls behind every
    button press, without the button group as a whole changing its
    place).

    Sorting happens in Python and not in SQL, because both ranks come
    from `clusters.yaml`: SQLite does not know them, and mirroring them as
    columns in `signal` would mean having to update them on every
    change to the YAML file - a second truth for the same value.

    The trailing members are the previous key. It is already unique
    because of the UNIQUE constraint on `signal`, which makes this
    key total too - the order never flickers, which matters for the
    export (it writes it into a file).
    """
    return (
        rank_for(signal.ref.cluster_id),
        signal.ref.endpoint,
        signal.ref.cluster_id,
        element_rank_for(signal.ref),
        signal.ref.element_id,
        signal.ref.kind.value,
    )


def _normalized_room(room: str | None) -> str | None:
    """A room name without leading/trailing whitespace; whatever is empty
    afterward becomes `None`.

    One place instead of three: `set_room`, `register_device` and
    `rename_room` ask the same question, and a room " Kitchen" next to
    "Kitchen" would be two rooms in the UI without anyone seeing the
    difference."""
    if room is None:
        return None
    return room.strip() or None


@dataclass(frozen=True)
class StoredDevice:
    """A row from `device` (Spec 5) - for the device API (Task 2, Phase 5).

    Deliberately carries no `online` status: reachability is runtime state
    (`Runtime`, fed from Matter subscriptions), not a stored property. An
    `online` field frozen here could be stale after a bridge restart until
    the next subscription arrives.
    """

    id: int
    node_id: int
    unique_id: str
    label: str
    # exported_at/updated_at (Task 5, Phase 5) - the basis for `GET
    # /api/export/status`. Both are ISO 8601 timestamps as text, `None`
    # means "never exported" or "not touched since registration"
    # respectively (see `_migrate_to_v2` for the case of a legacy
    # database). `updated_at` is deliberately coarse: it does not
    # distinguish WHAT changed on the device (label, a signal title, a
    # newly discovered signal list, ...), only THAT something might have
    # changed since the last export - that is enough for "changed since
    # then: yes/no".
    exported_at: str | None
    updated_at: str | None
    # Room and device types (device tab design, 2026-09-05). `room` is a
    # freely chosen name, `None` means "no room" - there is deliberately no
    # room table, a room exists for exactly as long as an active device
    # carries its name.
    #
    # `device_types` carries the RAW information from the device (endpoint
    # -> Matter type IDs), not the category derived from it. The reason
    # lies in this module's history: `signal.functional` and `signal.title`
    # were stored derivations, and `_migrate_to_v3` had to retroactively
    # recompute them for existing rows when the rule improved. A mapping
    # table from Matter type to category will grow; if only the source is
    # stored, that is a code change without a migration.
    room: str | None
    device_types: dict[int, frozenset[int]] | None


@dataclass(frozen=True)
class StoredGroup:
    """A row from `device_group` (design 2026-09-10, section 2).

    Carries no member list and no command list: both are separate tables
    with their own lifetime, and a copy frozen in here would be stale the
    moment a member changes. `category` is the `value` of a
    `profiles.categories.Category`, stored rather than derived - an
    emptied group must still know what it accepts (design 2.1).
    """

    id: int
    label: str
    room: str | None
    category: str
    exported_at: str | None
    updated_at: str | None


class UnknownCommandError(KeyError):
    """`KeyError.__str__` wraps the message in `repr()`, which makes
    `str(exc)` put extra quote marks around the entire text - Task 6 turns
    this into an HTTP error body that should not show that wrapping. The
    subclass returns the message unchanged; `pytest.raises(KeyError, ...)`
    still catches it, since it inherits from `KeyError`."""

    def __str__(self) -> str:
        return str(self.args[0])


class UnknownDeviceError(KeyError):
    """Like `UnknownCommandError`, for an unknown or already removed
    (`forget_device`) device - the same rationale: the device API (Task 2)
    turns this into an HTTP 404 body that should not carry the
    `repr()` quote marks from `KeyError.__str__`."""

    def __str__(self) -> str:
        return str(self.args[0])


class UnknownGroupError(KeyError):
    """Like `UnknownCommandError`: `KeyError.__str__` would wrap the
    message in `repr()`, and this text becomes an HTTP 404 body."""

    def __str__(self) -> str:
        return str(self.args[0])


class CategoryMismatchError(ValueError):
    """A member whose category differs from the group's (design 2.1)."""


class Store:
    def __init__(self, path: Path | str) -> None:
        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._db.commit()
        _migrate(self._db)
        # A view onto the same connection, not a second connection - see
        # the module docstring of `auth_store.py`.
        self.auth = AuthStore(self._db)
        # A view onto the same connection - see `settings_store.py`.
        self.settings = BridgeSettingsStore(
            self._db, default_udp_port=DEFAULT_UDP_PORT, default_listen_port=DEFAULT_LISTEN_PORT
        )
        # A view onto the same connection - see `locale_store.py`.
        self.locale = LocaleStore(self._db)
        # A view onto the same connection - see `resend_settings_store.py`.
        self.resend_settings = ResendSettingsStore(self._db)
        # Same connection again - see `update_settings_store.py`.
        self.update_settings = UpdateSettingsStore(self._db)

    def close(self) -> None:
        self._db.close()

    def check_writable(self) -> None:
        """Checks whether the database is actually writable RIGHT NOW - not
        just according to filesystem bits, but through a real write
        attempt that is immediately rolled back. Raises (typically
        `sqlite3.OperationalError`) for a read-only store, a full disk, or
        a database exclusively locked by another process; changes nothing
        about the data on success.

        For the diagnostics system check (Spec 10.5, see
        `api.diagnostics._check_store`) - the only caller so far.

        **Beforehand: any already-open implicit transaction is rolled back
        (review fix Important, 2026-09-02).** A few writing methods of this
        class (`rename_device`, `mark_exported`, `set_title`,
        `set_exported`) do not wrap their `UPDATE ...` plus `commit()` in
        their own try/except - unlike e.g. `register_signals`, which
        explicitly rolls back on `ValueError`/`sqlite3.Error`. If the
        `UPDATE` itself fails there, or even only the `commit()` does (e.g.
        a full disk), the transaction Python automatically opened BEFORE
        the `UPDATE` on this connection stays open. A second `BEGIN
        IMMEDIATE` right after that would then ALWAYS fail with
        `sqlite3.OperationalError: cannot start a transaction within a
        transaction` - regardless of whether the database has since become
        writable again. Without the handling here, the system check would
        incorrectly report exactly this case as "not writable", even
        though the database itself may be fine.

        Rolling back here is safe: every store instance belongs to exactly
        one thread and one event loop, and every writing method is purely
        synchronous - it never hangs on an `await` in the middle of its own
        transaction. An open transaction found at the time of THIS call can
        therefore never be an actually still-running, legitimate
        transaction - it is always the remainder of an already-failed,
        never-committed write attempt whose exception has already been
        passed on to its own caller. Rolling it back therefore is
        guaranteed never to discard any successfully written data."""
        if self._db.in_transaction:
            self._db.rollback()
        self._db.execute("BEGIN IMMEDIATE")
        self._db.rollback()

    @staticmethod
    def _now() -> str:
        """Thin bridge to `loxmatter.timestamps.now_iso` (review fix Minor,
        2026-09-02 - see there for the rationale why this function is no
        longer implemented on its own). Kept as its own method because
        `self._now()` is already wired up in many places in this class."""
        return now_iso()

    def _device_identity(self, snapshot: NodeSnapshot) -> str:
        """Falls back to the node ID: some devices do not report a unique ID (Spec 7.2)."""
        return snapshot.unique_id or f"node:{snapshot.node_id}"

    def register_device(self, snapshot: NodeSnapshot, room: str | None = None) -> int:
        """Creates a device, or returns the id of an already known active
        device without changing it.

        **`room` only takes effect on a newly inserted row.** If the
        device is already actively registered, the early return path below
        kicks in BEFORE the INSERT, and `room` is silently discarded there
        - deliberate: a known device keeps its maintained room on
        recommissioning, and a recommissioning without a room choice must
        not clear it. An explicit room choice for an already known device
        is therefore the caller's responsibility: the commissioning route
        in `api/devices.py` records it after this method via `set_room` if
        `request.room is not None` (review finding, finding 4) - never via
        `rename_device`, because the room ends up in no export template."""
        identity = self._device_identity(snapshot)
        row = self._db.execute(
            "SELECT id FROM device WHERE unique_id = ? AND active = 1", (identity,)
        ).fetchone()
        if row is not None:
            return int(row["id"])

        label = f"{snapshot.vendor_name} {snapshot.product_name}".strip() or identity
        cur = self._db.execute(
            "INSERT INTO device"
            " (unique_id, node_id, label, udp_port, updated_at, room, device_types)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                identity,
                snapshot.node_id,
                label,
                DEFAULT_UDP_PORT,
                self._now(),
                _normalized_room(room),
                _encode_device_types(device_types_by_endpoint(snapshot)),
            ),
        )
        self._db.commit()
        device_id = cur.lastrowid
        assert device_id is not None
        return int(device_id)

    def forget_device(self, device_id: int) -> None:
        """Marks a device as removed. The id stays assigned (Spec 6.2).

        Its group memberships do NOT stay: `register_device` matches on
        `unique_id AND active = 1`, so recommissioning the same physical
        device produces a NEW row with a new id - the old membership
        could therefore never come back to life, and would only sit
        around pointing at a device nobody can reach.

        Removing a device is a membership change like any other group
        member removal (design 4.3): every group the device belonged to
        recomputes its command intersection. The affected group ids are
        captured BEFORE the `DELETE FROM device_group_member` below - once
        that row is gone, there is no way left to ask which groups this
        device used to belong to. This does not delete a thereby-emptied
        group; a group survives losing every member (design 2.1, 4.3).

        **Rollback guard.** This method was a single UPDATE before device
        groups; this branch made it a two-write method (the
        `device_group_member` DELETE, then the `device` UPDATE) and needs
        the same `try`/`except (ValueError, sqlite3.Error):
        self._db.rollback(); raise` guard as `create_group`,
        `set_group_members` and `register_group_commands`. Without it, a
        write-time failure on the UPDATE would leave the DELETE sitting in
        the connection's open implicit transaction - the membership rows
        gone from this connection's view while `device.active` still
        reads 1 - for a later, unrelated `commit()` anywhere else in
        `Store` to flush that half-removed state to disk by surprise.
        """
        affected = [
            int(row["group_id"])
            for row in self._db.execute(
                "SELECT group_id FROM device_group_member WHERE device_id = ?", (device_id,)
            ).fetchall()
        ]
        try:
            self._db.execute("DELETE FROM device_group_member WHERE device_id = ?", (device_id,))
            self._db.execute("UPDATE device SET active = 0 WHERE id = ?", (device_id,))
        except (ValueError, sqlite3.Error):
            self._db.rollback()
            raise
        self._db.commit()
        for group_id in affected:
            self.register_group_commands(group_id)

    def udp_port(self, device_id: int) -> int:
        row = self._db.execute("SELECT udp_port FROM device WHERE id = ?", (device_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown device {device_id}")
        return int(row["udp_port"])

    @staticmethod
    def _as_device(row: sqlite3.Row) -> StoredDevice:
        return StoredDevice(
            id=int(row["id"]),
            node_id=int(row["node_id"]),
            unique_id=str(row["unique_id"]),
            label=str(row["label"]),
            exported_at=row["exported_at"],
            updated_at=row["updated_at"],
            room=row["room"],
            device_types=_decode_device_types(row["device_types"]),
        )

    def devices(self) -> list[StoredDevice]:
        """All active devices (Task 2, Phase 5) - for `GET /api/devices`.

        A removed device (`forget_device`) no longer shows up here, exactly
        as with `device_id_for_node`."""
        rows = self._db.execute("SELECT * FROM device WHERE active = 1 ORDER BY id").fetchall()
        return [self._as_device(r) for r in rows]

    def device(self, device_id: int) -> StoredDevice:
        """A single active device - `UnknownDeviceError` if it was never
        registered or has since been removed."""
        row = self._db.execute(
            "SELECT * FROM device WHERE id = ? AND active = 1", (device_id,)
        ).fetchone()
        if row is None:
            raise UnknownDeviceError(i18n.t("api.errors.unknown_device", device_id=device_id))
        return self._as_device(row)

    @staticmethod
    def _as_group(row: sqlite3.Row) -> StoredGroup:
        return StoredGroup(
            id=int(row["id"]),
            label=str(row["label"]),
            room=row["room"],
            category=str(row["category"]),
            exported_at=row["exported_at"],
            updated_at=row["updated_at"],
        )

    def _category_of(self, device_id: int) -> str:
        return category_for(self.device(device_id).device_types).value

    def _check_members(self, category: str, member_ids: Sequence[int]) -> None:
        """Every member must exist, be active and match `category`.

        Checked BEFORE anything is written, so a category mismatch never
        leaves a half-applied membership behind. This alone is not the
        whole all-or-nothing story, though: it only catches a category
        mismatch, not a duplicate id (checked separately by the caller) or
        a write-time SQLite error. `create_group` and `set_group_members`
        cover those by running their write loop inside the same
        `try`/`except (ValueError, sqlite3.Error): self._db.rollback();
        raise` guard as `register_commands` - together, the two give both
        methods the same all-or-nothing stance.
        """
        for device_id in member_ids:
            actual = self._category_of(device_id)
            if actual != category:
                raise CategoryMismatchError(
                    i18n.t(
                        "api.errors.group_category_mismatch",
                        device_id=device_id,
                        actual=actual,
                        expected=category,
                    )
                )

    def create_group(
        self, label: str, member_ids: Sequence[int], room: str | None = None
    ) -> StoredGroup:
        """The first member fixes the category, so there must be one.

        Duplicate ids in `member_ids` are rejected here, before anything is
        written: `device_group_member` has `UNIQUE (group_id, device_id)`,
        so an unchecked duplicate would only surface as a raw
        `sqlite3.IntegrityError` partway through the insert loop below.
        That loop runs as one transaction, exactly like
        `register_commands`: if a write still fails there - that dedup
        check missing a case, or any other SQLite error - the whole group
        is rolled back instead of leaving `device_group` and a partial
        `device_group_member` row set sitting in the connection's open
        transaction, to be committed by surprise the next time some
        unrelated write calls `commit()`.
        """
        if not member_ids:
            raise ValueError(i18n.t("api.errors.group_needs_a_member"))
        if len(set(member_ids)) != len(member_ids):
            raise ValueError(i18n.t("api.errors.group_duplicate_member"))
        category = self._category_of(member_ids[0])
        self._check_members(category, member_ids)
        try:
            cur = self._db.execute(
                "INSERT INTO device_group (label, room, category, updated_at) VALUES (?, ?, ?, ?)",
                (label, _normalized_room(room), category, self._now()),
            )
            group_id = cur.lastrowid
            assert group_id is not None
            for device_id in member_ids:
                self._db.execute(
                    "INSERT INTO device_group_member (group_id, device_id) VALUES (?, ?)",
                    (int(group_id), device_id),
                )
        except (ValueError, sqlite3.Error):
            self._db.rollback()
            raise
        self._db.commit()
        # A brand-new group needs its command list computed too - it does
        # not fall out of the INSERTs above for free (design 4.3).
        self.register_group_commands(int(group_id))
        return self.group(int(group_id))

    def groups(self) -> list[StoredGroup]:
        rows = self._db.execute("SELECT * FROM device_group ORDER BY id").fetchall()
        return [self._as_group(r) for r in rows]

    def group(self, group_id: int) -> StoredGroup:
        row = self._db.execute("SELECT * FROM device_group WHERE id = ?", (group_id,)).fetchone()
        if row is None:
            raise UnknownGroupError(i18n.t("api.errors.unknown_group", group_id=group_id))
        return self._as_group(row)

    def rename_group(self, group_id: int, label: str) -> None:
        self.group(group_id)
        self._db.execute(
            "UPDATE device_group SET label = ?, updated_at = ? WHERE id = ?",
            (label, self._now(), group_id),
        )
        self._db.commit()

    def set_group_room(self, group_id: int, room: str | None) -> None:
        self.group(group_id)
        self._db.execute(
            "UPDATE device_group SET room = ?, updated_at = ? WHERE id = ?",
            (_normalized_room(room), self._now(), group_id),
        )
        self._db.commit()

    def delete_group(self, group_id: int) -> None:
        """Removes a group and everything that references it.

        Three DELETEs, one commit: the same shape as `create_group` and
        `set_group_members`, and the same guard. Without
        `self._db.rollback()` in the `except` clause, a write-time failure
        on the second or third DELETE (a full disk, a corrupted index -
        anything past the first) would leave the earlier DELETE(s) sitting
        in the connection's open implicit transaction - the group's
        commands or memberships gone from this connection's view, the
        group row itself still there - for a later, unrelated `commit()`
        anywhere else in `Store` to flush that half-deleted state to disk
        by surprise.
        """
        self.group(group_id)
        try:
            self._db.execute("DELETE FROM group_command WHERE group_id = ?", (group_id,))
            self._db.execute("DELETE FROM device_group_member WHERE group_id = ?", (group_id,))
            self._db.execute("DELETE FROM device_group WHERE id = ?", (group_id,))
        except (ValueError, sqlite3.Error):
            self._db.rollback()
            raise
        self._db.commit()

    def group_members(self, group_id: int) -> list[StoredDevice]:
        """Active members only.

        `forget_device` already deletes the membership rows (see there),
        so this filter should never have anything to do. It is here
        anyway: a read that cannot return a removed device makes the
        correctness of that write not load-bearing.
        """
        self.group(group_id)
        rows = self._db.execute(
            "SELECT d.* FROM device_group_member m"
            " JOIN device d ON d.id = m.device_id"
            " WHERE m.group_id = ? AND d.active = 1"
            " ORDER BY d.id",
            (group_id,),
        ).fetchall()
        return [self._as_device(r) for r in rows]

    def set_group_members(self, group_id: int, member_ids: Sequence[int]) -> None:
        """Replaces the whole membership in one transaction.

        The complete list rather than add/remove: the command
        intersection is recomputed after every change anyway, and two
        single removals would recompute it twice and pass through an
        intermediate state nobody asked for - including keys that
        briefly vanish and come back (design 5).

        Duplicate ids in `member_ids` are rejected up front, for the same
        reason as in `create_group`: `device_group_member`'s
        `UNIQUE (group_id, device_id)` would otherwise turn an unchecked
        duplicate into an `sqlite3.IntegrityError` partway through the
        insert loop below. The DELETE and the inserts that follow run
        inside the same `try`/`except (ValueError, sqlite3.Error)` guard as
        `register_commands`, so a write-time failure there rolls back to
        the previous membership instead of leaving it half replaced -
        deleted, but not yet fully reinserted - for a later unrelated
        commit to persist.
        """
        group = self.group(group_id)
        # Same order as `create_group`: duplicates first, category second -
        # so input that is both duplicated and wrong-category raises the
        # same error, from the same check, in both methods.
        if len(set(member_ids)) != len(member_ids):
            raise ValueError(i18n.t("api.errors.group_duplicate_member"))
        self._check_members(group.category, member_ids)
        try:
            self._db.execute("DELETE FROM device_group_member WHERE group_id = ?", (group_id,))
            for device_id in member_ids:
                self._db.execute(
                    "INSERT INTO device_group_member (group_id, device_id) VALUES (?, ?)",
                    (group_id, device_id),
                )
            self._db.execute(
                "UPDATE device_group SET updated_at = ? WHERE id = ?", (self._now(), group_id)
            )
        except (ValueError, sqlite3.Error):
            self._db.rollback()
            raise
        self._db.commit()
        # The intersection depends on WHO the members are, so a membership
        # change must recompute it (design 4.3) - same call as at the end
        # of `create_group`.
        self.register_group_commands(group_id)

    @staticmethod
    def _as_group_command(row: sqlite3.Row) -> StoredGroupCommand:
        return StoredGroupCommand(
            key=str(row["key"]),
            slug=str(row["slug"]),
            group_id=int(row["group_id"]),
            cluster_id=int(row["cluster_id"]),
            command_id=int(row["command_id"]),
            takes_value=bool(row["takes_value"]),
        )

    def group_commands(self, group_id: int) -> list[StoredGroupCommand]:
        rows = self._db.execute(
            "SELECT * FROM group_command WHERE group_id = ? ORDER BY cluster_id, command_id",
            (group_id,),
        ).fetchall()
        return [self._as_group_command(r) for r in rows]

    def resolve_group_command(self, key: str) -> StoredGroupCommand:
        row = self._db.execute("SELECT * FROM group_command WHERE key = ?", (key,)).fetchone()
        if row is None:
            raise UnknownCommandError(i18n.t("api.errors.unknown_command", command_key=key))
        return self._as_group_command(row)

    def register_group_commands(self, group_id: int) -> list[StoredGroupCommand]:
        """Recomputes the group's command list from its current members.

        Called after every membership change. Mirrors `register_commands`,
        which likewise re-adopts `slug` and `takes_value` on every call so
        that a correction in `clusters.yaml` reaches an already stored
        command - a frozen list would be the opposite of that and would
        let a group claim a capability no member has left (design 4.3).

        A command that survives keeps its key. One that drops out of the
        intersection loses its row and its key answers 404 from then on -
        deliberately, because that 404 stands in the log and points at the
        one line in the Loxone project that needs attention, whereas a
        silently vanished key leaves an output nobody can trace.

        The intersection runs over `(cluster_id, command_id)` pairs, NOT
        over endpoints: a member that carries the pair on several
        endpoints still counts as one member that accepts it (see
        `group_targets`, which then sends to all of them).

        **Rollback guard (Task 1 review finding, reapplied here).** The
        DELETE and INSERT/UPDATE loops below run inside the same
        `try`/`except (ValueError, sqlite3.Error): self._db.rollback();
        raise` guard as `register_commands`, `create_group` and
        `set_group_members`. Task 1's plan had this method's shape without
        it: a write-time failure partway through the loops would leave the
        earlier writes sitting in the connection's open implicit
        transaction - not committed, but not rolled back either - for the
        next unrelated `commit()` anywhere else in `Store` to flush to disk
        by surprise. Every other multi-row writer in this file already
        carries this guard; this one is no exception.

        **`updated_at`, but only when something moved (design 4.3, review
        gap).** This method runs on every membership change, including
        ones that leave the intersection exactly as it was -
        `set_group_members` re-submitting the member list it already has,
        say, recomputes nothing. If this stamped `device_group.updated_at`
        unconditionally, every group would read "changed since the last
        export" permanently, which tells the export tab nothing, same as
        never stamping at all. So a `changed` flag tracks whether the
        loops below actually inserted a row, deleted a row, or altered an
        existing row's `slug`
        or `takes_value` - a surviving row's refresh UPDATE still runs
        unconditionally, exactly as before (see the comment at that write),
        but only counts toward `changed` when the values it writes differ
        from what was already there. The stamp only fires when the flag is
        set - in particular, when `forget_device` shrinks a group's
        intersection by dropping a member, so the group correctly stops
        looking unchanged even though nothing about the group's own row
        (label, room, membership list) was touched here.
        """
        members = self.group_members(group_id)
        by_member = [
            {(c.cluster_id, c.command_id): c for c in self.commands(device.id)}
            for device in members
        ]
        shared: dict[tuple[int, int], StoredCommand] = {}
        if by_member:
            common = set(by_member[0])
            for other in by_member[1:]:
                common &= set(other)
            shared = {pair: by_member[0][pair] for pair in common}

        keep = set(shared)
        changed = False
        try:
            for existing in self.group_commands(group_id):
                if (existing.cluster_id, existing.command_id) not in keep:
                    self._db.execute("DELETE FROM group_command WHERE key = ?", (existing.key,))
                    changed = True

            for (cluster_id, command_id), sample in sorted(shared.items()):
                row = self._db.execute(
                    "SELECT key, slug, takes_value FROM group_command"
                    " WHERE group_id = ? AND cluster_id = ? AND command_id = ?",
                    (group_id, cluster_id, command_id),
                ).fetchone()
                if row is not None:
                    # The refresh write itself stays unconditional - same
                    # reasoning as `register_commands` (re-adopt on every
                    # call so a `clusters.yaml` correction reaches an
                    # already-stored row). Only the `changed` bookkeeping
                    # is conditional: comparing the fetched values against
                    # `sample` BEFORE writing tells us whether this
                    # refresh actually altered anything, without skipping
                    # the write that `register_group_commands`'s docstring
                    # and the rollback-guard test both depend on always
                    # happening for a surviving row.
                    if row["slug"] != sample.slug or bool(row["takes_value"]) != sample.takes_value:
                        changed = True
                    self._db.execute(
                        "UPDATE group_command SET slug = ?, takes_value = ? WHERE key = ?",
                        (sample.slug, int(sample.takes_value), row["key"]),
                    )
                    continue
                changed = True
                self._db.execute(
                    "INSERT INTO group_command"
                    " (group_id, cluster_id, command_id, key, slug, takes_value)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        group_id,
                        cluster_id,
                        command_id,
                        f"g{group_id}_{sample.slug}",
                        sample.slug,
                        int(sample.takes_value),
                    ),
                )
            if changed:
                self._db.execute(
                    "UPDATE device_group SET updated_at = ? WHERE id = ?",
                    (self._now(), group_id),
                )
        except (ValueError, sqlite3.Error):
            self._db.rollback()
            raise
        self._db.commit()
        return self.group_commands(group_id)

    def group_targets(self, command: StoredGroupCommand) -> list[GroupTarget]:
        """The per-member command rows for one group command.

        A member may carry the pair on several endpoints; all of them are
        returned, ordered by endpoint, and all of them get the command
        (design 4.3). Members without a matching row are skipped rather
        than returned empty - by construction of the intersection there
        should be none, and an empty target would only make the
        dispatcher guard against a case the store already rules out.
        """
        targets: list[GroupTarget] = []
        for device in self.group_members(command.group_id):
            rows = tuple(
                stored
                for stored in self.commands(device.id)
                if stored.cluster_id == command.cluster_id
                and stored.command_id == command.command_id
            )
            if not rows:
                continue
            targets.append(
                GroupTarget(device_id=device.id, device_label=device.label, commands=rows)
            )
        return targets

    def rename_device(self, device_id: int, label: str) -> None:
        """Sets a device's label (`PATCH /api/devices/{device_id}`).

        Like `set_title`, with no prior existence check - the caller (the
        API route) checks via `device()` itself and reports an unknown
        device as 404 before this method is even called.

        Sets `updated_at` (Task 5, Phase 5): a rename ends up in the next
        export as a new `Title` in the template - `GET /api/export/status`
        should then list the device as "changed since then", even if no
        signal is affected."""
        self._db.execute(
            "UPDATE device SET label = ?, updated_at = ? WHERE id = ?",
            (label, self._now(), device_id),
        )
        self._db.commit()

    def set_room(self, device_id: int, room: str | None) -> None:
        """Sets a device's room (`PATCH /api/devices/{device_id}`).

        **Deliberately does NOT touch `updated_at`** - the one point where
        this method deviates from `rename_device` directly above it. That
        one's docstring gives the reason for the opposite: the label ends
        up in the next export as `Title` in the template, so `GET
        /api/export/status` rightly lists the device afterward as "changed
        since then". The room ends up in no template. If it also set
        `updated_at`, the first cleanup of room assignments would give
        every device an amber pill and a prompt for an export that produces
        exactly the same files as the last one.

        Like `rename_device`, with no existence check: the calling route
        checks via `device()` and reports 404 before it gets here."""
        self._db.execute(
            "UPDATE device SET room = ? WHERE id = ?", (_normalized_room(room), device_id)
        )
        self._db.commit()

    def backfill_commands(self, snapshots: Sequence[NodeSnapshot]) -> int:
        """Backfills commands that did not yet exist at commissioning time, and
        returns how many devices gained something.

        Called at bridge startup, alongside `backfill_device_types` -
        the snapshots of all reachable nodes have already been fetched there.

        **The case this addresses** (operations, September 8, 2026): a
        device's command list is created at commissioning time, from
        `extract_commands` against the then-current state of `clusters.yaml`.
        A command that was not in the table back then was discarded
        - and a later update that unlocks it never reached the
        device: `register_commands` only ran at commissioning time and at
        CLI export. An RGB light thereby kept its missing
        color control, even though the bridge had long known the command.
        The only way out was a manual export - which no one thinks of,
        because nothing points to it.

        Signals never had this hole: `Runtime.on_node_snapshot` calls
        `register_signals` on every refreshed snapshot. This method
        closes the same gap for commands.

        **Writes on every startup, not only when something is missing.**
        `register_commands` re-adopts `slug` and `takes_value` for
        known commands (see there) - that is exactly what it is built
        for, and a correction in `clusters.yaml` should reach an existing
        device even when no command is missing, but one is merely
        named differently. The price is a handful of UPDATEs per device
        and startup. The return value nonetheless only counts the
        devices where a command was actually ADDED - that is the
        change worth reporting, not the refresh.

        Keys stay untouched: `register_commands` only assigns them
        for new commands and leaves existing rows at their
        key. Otherwise this method would be dangerous rather than useful -
        the key is the wiring in Loxone, and this here runs
        on every startup.

        A device that is currently offline and therefore missing from
        `snapshots()` is skipped - the same rule as for
        `backfill_device_types`: this fills in, never clears.
        """
        by_node = {snapshot.node_id: snapshot for snapshot in snapshots}
        gained = 0
        for device in self.devices():
            snapshot = by_node.get(device.node_id)
            if snapshot is None:
                continue
            before = len(self.commands(device.id))
            self.register_commands(device.id, extract_commands(snapshot), device.node_id)
            if len(self.commands(device.id)) > before:
                gained += 1
        return gained

    def backfill_device_types(self, snapshots: Sequence[NodeSnapshot]) -> int:
        """Backfills `device.device_types` for devices that do not yet have
        any, and returns how many that was.

        Called when the bridge starts, right next to
        `runtime.seed_from_snapshot(await client.snapshots())` (`cli.py`) -
        the snapshots of all reachable nodes are already fetched there, a
        second fetch would be pure waste.

        **Only `device_types IS NULL`.** A device already backfilled is not
        rewritten on every start, and a device that happens to be offline
        and therefore missing from `snapshots()` does not lose its types -
        this only ever fills, never clears.

        Whether a device whose types change on a repeated interview (e.g.
        after a firmware update) should get an update is deliberately left
        open (design, open point 2): the case has never been observed and
        gets no mechanism on suspicion.

        Does not touch `updated_at` - the same rationale as for `set_room`:
        the device types end up in no export template."""
        by_node = {snapshot.node_id: snapshot for snapshot in snapshots}
        rows = self._db.execute(
            "SELECT id, node_id FROM device WHERE device_types IS NULL AND active = 1"
        ).fetchall()
        filled = 0
        for row in rows:
            snapshot = by_node.get(int(row["node_id"]))
            if snapshot is None:
                continue
            self._db.execute(
                "UPDATE device SET device_types = ? WHERE id = ?",
                (_encode_device_types(device_types_by_endpoint(snapshot)), int(row["id"])),
            )
            filled += 1
        self._db.commit()
        return filled

    def rename_room(self, old: str, new: str) -> int:
        """Renames a room across all active devices and returns how many
        that was (`POST /api/rooms/rename`).

        There is no room table (design 3.2), so "rename room" is not a
        write to one object but this one bulk write. The alternative would
        be typing a new room name into each device individually - with five
        devices, five opportunities for a typo that creates a sixth room.

        `active = 1` for the same reason `devices()` filters on it
        afterward: a removed device is no longer there from the UI's point
        of view.

        An already occupied target name merges both rooms; the confirmation
        prompt for that is the UI's responsibility, not this method's. An
        empty target name, by contrast, is rejected here: "rename" is not
        the way to dissolve a room - `set_room` with `None` on each
        individual device exists for that."""
        # `old` through the same normalization as `new`: since this method
        # became reachable via `POST /api/rooms/rename` (Task 5), the
        # source name arrives as free text from the JSON body, no longer
        # exclusively as an already-trimmed value read back from storage -
        # unfiltered, " Kitchen " would otherwise match zero rows and look
        # like a typo in a nonexistent room (review finding).
        source = _normalized_room(old)
        target = _normalized_room(new)
        if target is None:
            raise ValueError(i18n.t("api.devices.room_name_required"))
        cur = self._db.execute(
            "UPDATE device SET room = ? WHERE room = ? AND active = 1", (target, source)
        )
        self._db.commit()
        return int(cur.rowcount)

    def mark_exported(self, device_id: int) -> None:
        """Sets `exported_at` to now (Task 5, Phase 5).

        Called both from `api.export.download` and from `cli.py`'s
        `export` command - both write to the same database (see the
        module docstring of `api/export.py`), and `GET
        /api/export/status` should answer "when last exported"
        independently of which of the two paths the last export went
        through. Without this call in the CLI command, the WebUI would
        keep showing "never exported" after a `loxmatter export`."""
        self._db.execute("UPDATE device SET exported_at = ? WHERE id = ?", (self._now(), device_id))
        self._db.commit()

    def device_id_for_node(self, node_id: int) -> int | None:
        """Maps a Matter node ID to the associated, stable `device_id`.

        For the runtime (Task 8): an incoming subscription from
        matter-server carries only the node ID, but the signal keys hang
        off the `device_id` (see module docstring - a node ID can change,
        the `device_id` never does). `None` if no active device with this
        node ID is known, e.g. because it was never exported or has since
        been removed (`forget_device`) - a removed device's node ID must
        not point to its old, inactive `device_id`.
        """
        row = self._db.execute(
            "SELECT id FROM device WHERE node_id = ? AND active = 1", (node_id,)
        ).fetchone()
        return int(row["id"]) if row is not None else None

    def _existing_keys(self, device_id: int) -> set[str]:
        rows = self._db.execute(
            "SELECT key FROM signal WHERE device_id = ?", (device_id,)
        ).fetchall()
        return {str(r["key"]) for r in rows}

    def _assign_key(self, device_id: int, ref: SignalRef, slug: str, taken: set[str]) -> str:
        """Assigns a new, not-yet-taken key for ``ref``.

        The normal case is ``d<device_id>_<endpoint>_<slug>``. If that
        collides with a key already assigned to *another* signal of the
        same device, the element ID is appended - the only extra
        discriminator that is guaranteed to be unique per endpoint (see
        module docstring, Spec 6.2).
        """
        base = f"d{device_id}_{ref.endpoint}_{slug}"
        if base not in taken:
            return base
        disambiguated = f"{base}_{ref.element_id}"
        if disambiguated in taken:
            raise ValueError(
                f"key collision for device {device_id}: {disambiguated!r} already assigned"
            )
        return disambiguated

    def register_signals(self, device_id: int, snapshot: NodeSnapshot) -> list[StoredSignal]:
        """Creates new signals; known ones keep their key and title, but
        `unit` and `exportability` are redetermined on every call.

        Spec 6.2 explicitly requires immutability only for the key - not
        for `unit` or `exportability`. If those were frozen at first
        commissioning, a signal that only reports because no commissioning
        window happens to be open right now, or an attribute like
        `StartUpOnOff` that a device only populates later, would be stuck
        at `exportability=none` forever - and a fix in `clusters.yaml`
        would never reach an already stored signal. The only remedy would
        be deleting the entire database, which destroys every key. `title`,
        by contrast, stays untouched once `set_title` has set it - from
        then on it belongs to the user (see
        `test_key_survives_a_title_change`). Only when creating (the branch
        below without `existing`) is the title column filled once from
        `profile.title` - for a generic slug that is the plain-text name
        from the SDK catalog (`profiles.table.lookup`,
        `profiles.catalog.element_name`), otherwise the same value as
        `slug`.

        Runs as one transaction: if key assignment fails for a single new
        signal (see `_assign_key`), the entire registration is rolled back
        instead of leaving the device with a subset of its signals.
        Deliberately no `INSERT OR IGNORE` - that would not report a
        genuine key collision, but silently discard the second signal (see
        module docstring).
        """
        taken = self._existing_keys(device_id)
        device_types = device_types_by_endpoint(snapshot)
        try:
            for ref in extract_signals(snapshot):
                profile = lookup(ref, snapshot.attributes.get(ref.path))
                existing = self._db.execute(
                    "SELECT key FROM signal WHERE device_id = ? AND endpoint = ? AND cluster_id = ?"
                    " AND element_id = ? AND kind = ?",
                    (device_id, ref.endpoint, ref.cluster_id, ref.element_id, ref.kind.value),
                ).fetchone()
                if existing is not None:
                    # `functional` IS updated here, unlike `exported` (Task
                    # 8): it is a pure property of the device
                    # (`profiles.relevance.is_functional`), not toggleable
                    # by the user - a firmware update that changes an
                    # endpoint device type should be picked up here just
                    # like `unit`/`exportability`. See
                    # `StoredSignal.functional`.
                    self._db.execute(
                        "UPDATE signal SET unit = ?, exportability = ?, functional = ? WHERE key = ?",
                        (
                            profile.unit,
                            profile.exportability.value,
                            int(is_functional(ref, device_types)),
                            existing["key"],
                        ),
                    )
                    continue

                key = self._assign_key(device_id, ref, profile.slug, taken)
                taken.add(key)
                # Two questions, two answers (design 2026-09-03, 3):
                # `is_exportable` says whether the value fits a Loxone
                # input at all; `is_functional`, whether anyone wants it by
                # default. A Thread radio counter is the former and not
                # the latter.
                #
                # Only when CREATING: the UPDATE branch above continues to
                # leave `exported` alone once a signal is known - from then
                # on the value belongs to the user. `functional`, on the
                # other hand, never belongs to the user (see above) and is
                # therefore written in BOTH branches.
                functional = is_functional(ref, device_types)
                exported = is_exportable(profile.exportability) and functional
                self._db.execute(
                    "INSERT INTO signal "
                    "(device_id, endpoint, cluster_id, element_id, kind, key, title, unit,"
                    " exportability, exported, functional) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        device_id,
                        ref.endpoint,
                        ref.cluster_id,
                        ref.element_id,
                        ref.kind.value,
                        key,
                        profile.title,
                        profile.unit,
                        profile.exportability.value,
                        int(exported),
                        int(functional),
                    ),
                )
            # Mark the device as "changed since then" (Task 5, Phase 5): a
            # newly discovered signal, or one corrected in `unit`/
            # `exportability`, should reach `GET /api/export/status`, even
            # if `register_signals` itself did not assign a single new key
            # (a pure refresh of an already known device).
            self._db.execute(
                "UPDATE device SET updated_at = ? WHERE id = ?", (self._now(), device_id)
            )
        except (ValueError, sqlite3.Error):
            self._db.rollback()
            raise
        self._db.commit()
        return self.signals(device_id)

    def set_title(self, key: str, title: str) -> None:
        self._touch_owning_device(key)
        self._db.execute("UPDATE signal SET title = ? WHERE key = ?", (title, key))
        self._db.commit()

    def set_exported(self, key: str, exported: bool) -> None:
        """Sets a signal's export flag (`PATCH /api/signals/{key}`,
        Task 2). Like `set_title`, with no existence check - see there."""
        self._touch_owning_device(key)
        self._db.execute("UPDATE signal SET exported = ? WHERE key = ?", (int(exported), key))
        self._db.commit()

    def set_resend(self, key: str, resend: bool) -> None:
        """Sets a signal's resend flag (`PATCH /api/signals/{key}`,
        periodic resend design, 2026-09-04). Like `set_exported`, with no
        existence check - see there."""
        self._touch_owning_device(key)
        self._db.execute("UPDATE signal SET resend = ? WHERE key = ?", (int(resend), key))
        self._db.commit()

    def _touch_owning_device(self, signal_key: str) -> None:
        """Sets `updated_at` of the device that `signal_key` belongs to
        (Task 5, Phase 5) - `set_title`/`set_exported` do not get a
        `device_id` (see their docstrings), hence the subquery. An unknown
        key matches no row and stays a silent no-op, exactly like the
        subsequent `UPDATE signal` in both callers - the caller (the API
        route) already checks existence beforehand (see
        `api.devices.rename_signal`)."""
        self._db.execute(
            "UPDATE device SET updated_at = ?"
            " WHERE id = (SELECT device_id FROM signal WHERE key = ?)",
            (self._now(), signal_key),
        )

    @staticmethod
    def _as_signal(row: sqlite3.Row) -> StoredSignal:
        return StoredSignal(
            key=row["key"],
            ref=SignalRef(
                row["endpoint"], row["cluster_id"], row["element_id"], SignalKind(row["kind"])
            ),
            title=row["title"],
            unit=row["unit"],
            exportability=Exportability(row["exportability"]),
            device_id=int(row["device_id"]),
            exported=bool(row["exported"]),
            functional=bool(row["functional"]),
            resend=bool(row["resend"]),
        )

    def signals(self, device_id: int) -> list[StoredSignal]:
        """All signals of a device, sorted by meaning.

        The `ORDER BY` stays in place even though `_signal_order`
        overrides it: it pins down the row order already before sorting
        and thereby makes an error in `_signal_order` visible,
        instead of hiding it behind a random SQLite order.

        This order carries further than the UI: `to_inputs`
        in `api.export` writes it unchanged into the VIU template
        (design 2026-09-07, section 5). The project-file sync, by
        contrast, reconciles by key (`projectsync.diff._plan_inputs`),
        not by position - a changed order produces no
        phantom changes there.
        """
        rows = self._db.execute(
            "SELECT * FROM signal WHERE device_id = ?"
            " ORDER BY endpoint, cluster_id, element_id, kind",
            (device_id,),
        ).fetchall()
        return sorted((self._as_signal(r) for r in rows), key=_signal_order)

    def signal_by_key(self, key: str) -> StoredSignal | None:
        """A single signal by its key - for `PATCH /api/signals/{key}`
        (Task 2), which has no device path parameter and therefore cannot
        go via `signals(device_id)`. `None` instead of an exception,
        analogous to `device_id_for_node` - the caller decides whether that
        is a 404."""
        row = self._db.execute("SELECT * FROM signal WHERE key = ?", (key,)).fetchone()
        return self._as_signal(row) if row is not None else None

    def resend_keys(self) -> list[str]:
        """All signal keys with `resend = true`, across all ACTIVE devices
        - for `Runtime.resend_marked()` (periodic resend as opt-in, design
        2026-09-04). A signal of a removed device (`forget_device`) no
        longer shows up here, exactly as with `devices()`."""
        rows = self._db.execute(
            "SELECT signal.key FROM signal"
            " JOIN device ON device.id = signal.device_id"
            " WHERE signal.resend = 1 AND device.active = 1"
        ).fetchall()
        return [str(r["key"]) for r in rows]

    def _existing_command_keys(self, device_id: int) -> set[str]:
        rows = self._db.execute(
            "SELECT key FROM command WHERE device_id = ?", (device_id,)
        ).fetchall()
        return {str(r["key"]) for r in rows}

    def register_commands(
        self, device_id: int, commands: Sequence[DeviceCommand], node_id: int
    ) -> list[StoredCommand]:
        """Makes the exported command keys resolvable at runtime.

        Without this, the exporter would write keys into the template that
        nobody could later map back to a Matter command. The key is
        assembled exclusively here - the exporter (cli.py) takes over the
        result instead of building it a second time itself. Two places
        that assemble the same key independently would otherwise drift
        apart without any error reporting it.

        An already known command (same device_id/endpoint/cluster_id/
        command_id) keeps its key, but `takes_value` and `slug` are
        re-adopted on every call - exactly as `register_signals`
        redetermines `unit` and `exportability`. Otherwise a fix in
        `clusters.yaml` (a command that is subsequently given a value or
        renamed) would never reach an already stored command, and the only
        remedy would be deleting the entire database, which destroys every
        key.

        Runs as one transaction: if key assignment fails for a single new
        command, the entire registration is rolled back instead of leaving
        the device with a subset of its commands. Deliberately no `INSERT
        OR IGNORE` - that would not report a genuine key collision, but
        silently discard the second command (see module docstring and
        `register_signals`). Unlike with signals, there is no fallback
        strategy here via an extra ID: two commands of different clusters
        on the same endpoint with the same slug are a bug in
        `clusters.yaml`, not a legitimate ambiguity.
        """
        taken = self._existing_command_keys(device_id)
        try:
            for command in commands:
                existing = self._db.execute(
                    "SELECT key FROM command WHERE device_id = ? AND endpoint = ?"
                    " AND cluster_id = ? AND command_id = ?",
                    (device_id, command.endpoint, command.cluster_id, command.command_id),
                ).fetchone()
                if existing is not None:
                    self._db.execute(
                        "UPDATE command SET takes_value = ?, slug = ? WHERE key = ?",
                        (int(command.takes_value), command.slug, existing["key"]),
                    )
                    continue

                key = f"d{device_id}_{command.endpoint}_{command.slug}"
                if key in taken:
                    collision = self._db.execute(
                        "SELECT cluster_id, command_id FROM command"
                        " WHERE device_id = ? AND key = ?",
                        (device_id, key),
                    ).fetchone()
                    raise ValueError(
                        f"key collision for device {device_id}: command "
                        f"(cluster_id={command.cluster_id}, command_id={command.command_id}) "
                        f"and (cluster_id={collision['cluster_id']}, "
                        f"command_id={collision['command_id']}) share the "
                        f"key {key!r}"
                    )
                taken.add(key)
                self._db.execute(
                    "INSERT INTO command "
                    "(device_id, node_id, endpoint, cluster_id, command_id, key, slug,"
                    " takes_value) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        device_id,
                        node_id,
                        command.endpoint,
                        command.cluster_id,
                        command.command_id,
                        key,
                        command.slug,
                        int(command.takes_value),
                    ),
                )
            # Like at the end of `register_signals` (Task 5, Phase 5): even
            # a pure refresh without a new key counts as "changed since
            # then", e.g. when `clusters.yaml` subsequently assigns a
            # command `takes_value`.
            self._db.execute(
                "UPDATE device SET updated_at = ? WHERE id = ?", (self._now(), device_id)
            )
        except (ValueError, sqlite3.Error):
            self._db.rollback()
            raise
        self._db.commit()
        return self.commands(device_id)

    def commands(self, device_id: int) -> list[StoredCommand]:
        rows = self._db.execute(
            "SELECT * FROM command WHERE device_id = ? ORDER BY endpoint, cluster_id, command_id",
            (device_id,),
        ).fetchall()
        return [self._as_command(r) for r in rows]

    def resolve_command(self, key: str) -> StoredCommand:
        row = self._db.execute("SELECT * FROM command WHERE key = ?", (key,)).fetchone()
        if row is None:
            raise UnknownCommandError(i18n.t("api.errors.unknown_command", command_key=key))
        return self._as_command(row)

    @staticmethod
    def _as_command(row: sqlite3.Row) -> StoredCommand:
        return StoredCommand(
            key=row["key"],
            slug=row["slug"],
            node_id=int(row["node_id"]),
            endpoint=int(row["endpoint"]),
            cluster_id=int(row["cluster_id"]),
            command_id=int(row["command_id"]),
            takes_value=bool(row["takes_value"]),
            device_id=int(row["device_id"]),
        )

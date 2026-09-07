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

"""Names signals and decides whether they are exportable to Loxone.

Principle from Spec 3.5: the table enriches, it does not filter. An
unknown cluster gets a generic name and is still exported, provided its
value fits a Loxone input at all.

Spec 6.6: lists, structs and null values do not fit. They stay signals and
are visible in the UI, but never become Loxone objects.

Spec 7.3: `Unit` in the template is a format string for the Loxone UI
(`<v.N> unit`), not a unit label. `unit_format` carries this mapping as a
data table, not as a branch in the exporter.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from loxmatter.matter.models import SignalKind, SignalRef
from loxmatter.profiles.catalog import element_name

_TABLE_PATH = Path(__file__).with_name("clusters.yaml")


class Exportability(str, Enum):
    ANALOG = "analog"
    DIGITAL = "digital"
    TEXT = "text"
    NONE = "none"


@dataclass(frozen=True)
class Profile:
    slug: str
    title: str
    unit: str
    exportability: Exportability


_EXPORTABLE_KINDS = (Exportability.ANALOG, Exportability.DIGITAL)


def is_exportable(exportability: Exportability) -> bool:
    """Whether a signal with this exportability can become a Loxone input
    at all - analog or digital, otherwise not (Spec 6.6: text,
    lists/structs and null values do not fit).

    The single source for this rule (review fix Important #2, 2026-09-02):
    previously `Store.register_signals` replicated it for the default of
    `exported` as ``exportability is not Exportability.NONE`` - which
    incorrectly includes TEXT - while `api.devices` independently computed
    ``in (ANALOG, DIGITAL)``. Two copies of the same rule that drifted
    apart without any error reporting it. Now there is only this one
    function; `Store.register_signals` (default of `exported`),
    `api.devices._signal_out`/`_device_out` (`exportable`/
    `exportable_count`) and the migration to schema version 1
    (`model.store._migrate_to_v1`, retroactive backfill of existing rows)
    all call it.
    """
    return exportability in _EXPORTABLE_KINDS


def classify(value: object) -> Exportability:
    """Decides, based solely on the value, whether Loxone can take it in."""
    if isinstance(value, bool):
        return Exportability.DIGITAL
    if isinstance(value, (int, float)):
        return Exportability.ANALOG
    if isinstance(value, str):
        return Exportability.TEXT
    return Exportability.NONE


@functools.cache
def _table() -> dict[int, dict[str, Any]]:
    raw = yaml.safe_load(_TABLE_PATH.read_text(encoding="utf-8"))
    return {int(k): v for k, v in (raw.get("clusters") or {}).items()}


def knows_cluster(cluster_id: int) -> bool:
    """Whether the profile table carries this cluster at all."""
    return cluster_id in _table()


def known_attribute_section(cluster_id: int) -> bool:
    """Whether the table carries an `attributes:` section for this cluster
    at all - regardless of whether it names anything.

    Separate from `knows_cluster`, because `knows_cluster` only asks
    whether the cluster is in the table at all - a cluster can be in there
    and still say nothing about its attributes, if it was only maintained
    for its commands (cluster 768/ColorControl before the Phase 6
    follow-up fix: only `commands:`, no `attributes:`). Without this
    distinction, `profiles.relevance.is_functional` incorrectly read
    "the table knows the cluster" as "the table knows every one of its
    attributes" - `names_element` ALWAYS looks up unsuccessfully in a
    missing section (`cluster.get(section) or {}` becomes `{}`), and every
    attribute of a cluster maintained only for commands thereby counted as
    not functional, even though the table makes no statement about them at
    all. The trap is structural and not limited to cluster 768: any future
    cluster that enters the table only for a command or a unit would
    otherwise be silenced across all its attributes."""
    cluster = _table().get(cluster_id)
    return cluster is not None and cluster.get("attributes") is not None


def names_element(ref: SignalRef) -> bool:
    """Whether the profile table names exactly this element.

    Separate from `lookup`, because `lookup` invents a generic name for an
    unnamed element (`c6_a16387`) and thereby loses the distinction. The
    fine-grained selection in `profiles.relevance` needs it, though: within
    a known cluster, "named" is the marker for "wanted".
    """
    cluster = _table().get(ref.cluster_id)
    if cluster is None:
        return False
    section = "events" if ref.kind is SignalKind.EVENT else "attributes"
    return ref.element_id in (cluster.get(section) or {})


def struct_field(ref: SignalRef) -> int | None:
    """The field number to pull out of a struct - or None.

    Only for attributes of a cluster the table knows, where the entry
    carries a `field`.
    """
    if ref.kind is SignalKind.EVENT:
        return None
    cluster = _table().get(ref.cluster_id)
    if cluster is None:
        return None
    entry = (cluster.get("attributes") or {}).get(ref.element_id)
    if not entry:
        return None
    field = entry.get("field")
    return int(field) if field is not None else None


def struct_member(ref: SignalRef, raw: object) -> object:
    """The value on which classification and computation happen.

    Without a `field` entry, `raw` unchanged. With `field`, the named
    element of the struct - and `None` if the value is not a struct or the
    element is missing. The signal then stays non-exportable; it is NOT
    guessed at (design 2026-09-03, 5).

    matter-server returns structs as a dictionary with the field tag as a
    string (`{"0": ...}`); the number is accepted as well.

    This one function is the shared source for `lookup` (classification at
    commissioning time) and `loxone.values.to_loxone_value` (runtime). Two
    copies would drift apart and let the UI report a value the export does
    not know.
    """
    field = struct_field(ref)
    if field is None:
        return raw
    if not isinstance(raw, dict):
        return None
    return raw.get(str(field), raw.get(field))


def lookup(ref: SignalRef, value: object) -> Profile:
    """Returns name(s), unit and exportability for a signal.

    `slug` is key material (main document 6.2) and therefore always stays
    generic when the table itself does not name the element - it must
    never move, or an existing Loxone wiring dies silently. `title` is
    pure display: if the table names the element, it wins (the same
    brevity as the slug is fine there). Otherwise the SDK catalog
    (`profiles.catalog.element_name`) feeds the plain-text name from the
    Matter specification; if that does not know it either, `title` falls
    back to the generic slug - display and operation thereby work
    independently of whether the catalog is available at all (see its
    docstring).
    """
    cluster = _table().get(ref.cluster_id, {})
    section = "events" if ref.kind is SignalKind.EVENT else "attributes"
    entry = (cluster.get(section) or {}).get(ref.element_id)

    if ref.kind is SignalKind.EVENT:
        if entry:
            return Profile(
                slug=entry["slug"],
                title=entry["slug"],
                unit="",
                exportability=Exportability.DIGITAL,
            )
        slug = f"c{ref.cluster_id}_e{ref.element_id}"
        return Profile(
            slug=slug, title=element_name(ref) or slug, unit="", exportability=Exportability.DIGITAL
        )

    if entry:
        return Profile(
            slug=entry["slug"],
            title=entry["slug"],
            unit=entry.get("unit", ""),
            exportability=classify(struct_member(ref, value)),
        )
    slug = f"c{ref.cluster_id}_a{ref.element_id}"
    return Profile(
        slug=slug,
        title=element_name(ref) or slug,
        unit="",
        exportability=classify(value),
    )


# Decimal places per unit for the Loxone format string (Spec 7.3). Power is
# deliberately not grouped with the other physical quantities at 1
# decimal: from mW to kW is six orders of magnitude, and with <v.3> a
# 300 mW standby consumer disappears as 0.000 in the UI.
# Loxone accepts at most THREE decimal places in the format string; `<v.4>`
# and more does not work (verified on the Miniserver, 2026-09-03). This
# limit is the reason `unit_format` below caps the value.
MAX_LOXONE_DECIMALS = 3

_UNIT_DECIMALS: dict[str, int] = {
    # Power and energy used to be at 6 here, because there are six orders
    # of magnitude from mW to kW and a 300 mW standby consumer disappears
    # as 0.000 with three digits. But Loxone does not accept six, and a
    # format string that is not accepted is worse than a coarse display.
    # The VALUE itself is unaffected by this - the format string only
    # determines the presentation, function blocks and statistics compute
    # with the full number. Only what lies below one watt is visibly lost.
    "kW": MAX_LOXONE_DECIMALS,
    "kWh": MAX_LOXONE_DECIMALS,
    "°C": 1,
    "%": 1,
    "V": 1,
    "A": 1,
    # Hue in degrees and color temperature in mired are meaningfully integers.
    "°": 0,
    "mired": 0,
}

# Loxone writes no space before percent (`<v>%`), but does before every
# other unit (`<v.3> kW`, `<v.1> °C`) - confirmed against the 26 real
# templates from Spec 6.1.
_UNITS_WITHOUT_LEADING_SPACE: frozenset[str] = frozenset({"%"})


def unit_format(unit: str) -> str:
    """Loxone format string for a unit, or "" if none is known."""
    decimals = _UNIT_DECIMALS.get(unit)
    if decimals is None:
        return ""
    # Capped rather than just kept correct in the table: an entry with
    # more digits would otherwise be a format string the Miniserver does
    # not accept - and that would only surface at import time, not here.
    decimals = min(decimals, MAX_LOXONE_DECIMALS)
    separator = "" if unit in _UNITS_WITHOUT_LEADING_SPACE else " "
    return f"<v.{decimals}>{separator}{unit}"


# Clusters whose commands must never appear as a Loxone output. This list
# also applies in raw mode. Every entry is a deliberate decision, not an
# enumeration - the list is deliberately conservative: a cluster blocked
# wrongly costs one missing command, one wrongly forgotten can throw the
# device off the network.
ADMINISTRATIVE_CLUSTERS: frozenset[int] = frozenset(
    {
        31,  # AccessControl - governs who may even talk to the device at all
        41,  # OtaSoftwareUpdateProvider - ApplyUpdateRequest can force a firmware update
        42,  # OtaSoftwareUpdateRequestor - AnnounceOtaProvider triggers an update search
        48,  # GeneralCommissioning - commissioning
        49,  # NetworkCommissioning - commissioning
        50,  # DiagnosticLogs - RetrieveLogsRequest
        51,  # GeneralDiagnostics - TestEventTrigger
        52,  # SoftwareDiagnostics - ResetWatermarks resets diagnostic counters
        53,  # ThreadNetworkDiagnostics - ResetCounts resets diagnostic counters
        54,  # WiFiNetworkDiagnostics - ResetCounts resets diagnostic counters
        55,  # EthernetNetworkDiagnostics - ResetCounts resets diagnostic counters
        56,  # TimeSynchronization - SetUTCTime, SetTrustedTimeSource: a wrong clock
        #     breaks schedules and certificate validation
        60,  # AdministratorCommissioning - OpenCommissioningWindow opens the device for
        #     another fabric
        62,  # OperationalCredentials - RemoveFabric
        63,  # GroupKeyManagement - manages the groups' security keys
        70,  # IcdManagement - RegisterClient controls who may wake the device
    }
)


def scale_factor(ref: SignalRef) -> float:
    """Factor by which a raw Matter value converts into the Loxone unit.

    1.0 if the table says nothing - unknown clusters are passed through
    raw, not discarded (Spec 3.5).
    """
    cluster = _table().get(ref.cluster_id, {})
    entry = (cluster.get("attributes") or {}).get(ref.element_id)
    if not entry:
        return 1.0
    return float(entry.get("scale", 1.0))


def command_slug(cluster_id: int, command_id: int) -> str | None:
    """Name of a command, or None if it is not in the table."""
    entry = (_table().get(cluster_id, {}).get("commands") or {}).get(command_id)
    return entry["slug"] if entry else None


def command_takes_value(cluster_id: int, command_id: int) -> bool:
    """Whether the command expects a value (e.g. MoveToLevel), or none (e.g. Off)."""
    entry = (_table().get(cluster_id, {}).get("commands") or {}).get(command_id)
    return bool(entry and entry.get("takes_value"))


def known_command_pairs() -> set[tuple[int, int]]:
    """All (cluster ID, command ID) pairs that `clusters.yaml` carries
    under `commands`.

    Exists solely for the consistency test against
    `commands.translate._PAYLOAD_BUILDERS` (see there) - keeps both tables
    in sync instead of letting them silently drift apart. That is exactly
    what happened in review fix C2 (2026-09-02): cluster 768 command 10 was
    in `_PAYLOAD_BUILDERS` but missing here - the raw export built a
    digital command out of it whose payload builder in reality expected a
    value."""
    return {
        (cluster_id, command_id)
        for cluster_id, cluster in _table().items()
        for command_id in (cluster.get("commands") or {})
    }

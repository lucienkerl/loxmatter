# loxmatter - bindet Matter-Geraete an einen Loxone Miniserver an.
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

from loxmatter.matter.models import NodeSnapshot, SignalKind, SignalRef


def test_signal_ref_renders_matter_path():
    ref = SignalRef(endpoint=1, cluster_id=6, element_id=0, kind=SignalKind.ATTRIBUTE)
    assert ref.path == "1/6/0"


def test_signal_refs_sort_by_endpoint_then_cluster_then_element():
    unsorted = [
        SignalRef(2, 6, 0, SignalKind.ATTRIBUTE),
        SignalRef(1, 1030, 0, SignalKind.ATTRIBUTE),
        SignalRef(1, 6, 16, SignalKind.ATTRIBUTE),
        SignalRef(1, 6, 0, SignalKind.ATTRIBUTE),
    ]
    assert [r.path for r in sorted(unsorted)] == ["1/6/0", "1/6/16", "1/1030/0", "2/6/0"]


def test_signal_ref_is_hashable_and_frozen():
    ref = SignalRef(1, 6, 0, SignalKind.ATTRIBUTE)
    assert len({ref, SignalRef(1, 6, 0, SignalKind.ATTRIBUTE)}) == 1


def test_attribute_and_event_on_same_path_are_distinct():
    attribute = SignalRef(1, 6, 0, SignalKind.ATTRIBUTE)
    event = SignalRef(1, 6, 0, SignalKind.EVENT)
    assert attribute != event
    assert len({attribute, event}) == 2


def test_node_snapshot_reads_basic_information_cluster():
    raw = {
        "attributes": {
            "0/40/1": "IKEA of Sweden",
            "0/40/3": "TRADFRI bulb",
            "0/40/18": "ABC123",
            "1/6/0": True,
        }
    }
    snapshot = NodeSnapshot.from_raw(node_id=12, raw=raw)
    assert snapshot.node_id == 12
    assert snapshot.vendor_name == "IKEA of Sweden"
    assert snapshot.product_name == "TRADFRI bulb"
    assert snapshot.unique_id == "ABC123"
    assert snapshot.attributes["1/6/0"] is True


def test_node_snapshot_tolerates_missing_basic_information():
    snapshot = NodeSnapshot.from_raw(node_id=3, raw={"attributes": {"1/6/0": False}})
    assert snapshot.vendor_name == ""
    assert snapshot.product_name == ""
    assert snapshot.unique_id == ""

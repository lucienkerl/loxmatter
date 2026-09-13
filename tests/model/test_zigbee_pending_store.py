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

"""`ZigbeePendingStore` - the record of what a Zigbee device still owes.

The test that carries the module is the first one: the whole reason this
table exists rather than a set on the source is that a sleeping device may
not wake for hours, and a restart in between must not lose the fact that it
still has to be configured.

The counterpart test - that an INTERRUPTED configuration run leaves
retryable rows rather than a stuck state - lives in
`tests/zigbee/test_configure.py`, because it has to drive the real routine
and the Zigbee fakes are only importable from that directory."""

from __future__ import annotations

from loxmatter.matter.models import NodeSnapshot, Technology
from loxmatter.model.store import Store

CONTACT = "00:15:8d:00:02:aa:bb:cc"
MOTION = "00:15:8d:00:02:11:22:33"

IAS_ZONE = 0x0500
POWER_CONFIGURATION = 0x0001


def snapshot(technology: Technology, address: str) -> NodeSnapshot:
    """A registrable device of either technology. `unique_id` carries the
    address so two snapshots never collide on `device.unique_id`."""
    return NodeSnapshot(
        technology=technology,
        address=address,
        vendor_name="LUMI",
        product_name="lumi.sensor_magnet",
        unique_id=f"{technology}:{address}",
        attributes={},
    )


def test_a_pending_cluster_survives_a_restart(tmp_path):
    """The whole point. A `configure_reporting` to a SLEEPING device fails
    with TimeoutError or DeliveryError after up to ~28 s per attempt, and
    the device may not wake for hours. Holding "still to do" in memory would
    lose it on every restart, and the sensor would stay silent forever with
    nothing recording why.

    Fault to prove it: keep the set in memory on the source."""
    path = tmp_path / "loxmatter.sqlite"
    store = Store(path)
    store.zigbee_pending.mark_pending(CONTACT, 1, POWER_CONFIGURATION)
    store.zigbee_pending.mark_pending(CONTACT, 1, IAS_ZONE)
    store.close()

    # The bridge restarts - a new process, a new connection, nothing
    # carried over but the file.
    store = Store(path)
    try:
        assert store.zigbee_pending.pending_for(CONTACT) == [
            (1, POWER_CONFIGURATION),
            (1, IAS_ZONE),
        ]
        assert store.zigbee_pending.addresses_with_pending() == [CONTACT]
    finally:
        store.close()


def test_marking_the_same_cluster_twice_is_one_row(tmp_path):
    """A deferred cluster is marked again on every retry, and a sleepy
    sensor can be retried dozens of times before it answers.

    Fault to prove it: a plain `INSERT` without the conflict clause. The
    second attempt then raises `IntegrityError` from inside the routine that
    was meant to be the fail-safe path."""
    store = Store(tmp_path / "loxmatter.sqlite")
    try:
        for _ in range(3):
            store.zigbee_pending.mark_pending(CONTACT, 1, IAS_ZONE)
        assert store.zigbee_pending.pending_for(CONTACT) == [(1, IAS_ZONE)]
    finally:
        store.close()


def test_clearing_one_cluster_leaves_the_others(tmp_path):
    """The clusters of one device are configured one at a time and succeed
    one at a time - a contact sensor's IAS enrolment can go through while
    its battery reporting is still outstanding."""
    store = Store(tmp_path / "loxmatter.sqlite")
    try:
        store.zigbee_pending.mark_pending(CONTACT, 1, POWER_CONFIGURATION)
        store.zigbee_pending.mark_pending(CONTACT, 1, IAS_ZONE)
        store.zigbee_pending.mark_pending(CONTACT, 2, IAS_ZONE)

        store.zigbee_pending.clear(CONTACT, 1, IAS_ZONE)

        assert store.zigbee_pending.pending_for(CONTACT) == [
            (1, POWER_CONFIGURATION),
            (2, IAS_ZONE),
        ]
    finally:
        store.close()


def test_forgetting_a_device_forgets_its_pending_rows(tmp_path):
    """Fault to prove it: leave them. Removing and re-pairing a device then
    inherits the old device's unfinished business."""
    store = Store(tmp_path / "loxmatter.sqlite")
    try:
        store.zigbee_pending.mark_pending(CONTACT, 1, IAS_ZONE)
        store.zigbee_pending.mark_pending(MOTION, 1, IAS_ZONE)

        store.zigbee_pending.forget(CONTACT)

        assert store.zigbee_pending.pending_for(CONTACT) == []
        # And only that device's.
        assert store.zigbee_pending.pending_for(MOTION) == [(1, IAS_ZONE)]
        assert store.zigbee_pending.addresses_with_pending() == [MOTION]
    finally:
        store.close()


def test_removing_a_zigbee_device_forgets_its_pending_rows(tmp_path):
    """The same rule, through the door the user actually walks through:
    `Store.forget_device`, which is what the removal route calls.

    Fault to prove it: drop the `zigbee_pending.forget(address)` call from
    `forget_device`. The rows then outlive the device, and re-pairing the
    same sensor inherits work that belongs to bindings it no longer has."""
    store = Store(tmp_path / "loxmatter.sqlite")
    try:
        device_id = store.register_device(snapshot("zigbee", CONTACT))
        store.zigbee_pending.mark_pending(CONTACT, 1, IAS_ZONE)

        store.forget_device(device_id)

        assert store.zigbee_pending.pending_for(CONTACT) == []
    finally:
        store.close()


def test_a_matter_devices_removal_never_touches_a_zigbee_devices_rows(tmp_path):
    """A Matter device's address is a node id - a small integer as text -
    and handing that to `ZigbeePendingStore.forget` would delete the rows of
    whatever Zigbee device happened to share it.

    Fault to prove it: drop `AND technology = 'zigbee'` from
    `Store._zigbee_address` and register the Zigbee sensor under the address
    `"7"`. The Matter device's removal then clears the sensor's rows."""
    store = Store(tmp_path / "loxmatter.sqlite")
    try:
        matter_id = store.register_device(snapshot("matter", "7"))
        store.zigbee_pending.mark_pending("7", 1, IAS_ZONE)

        store.forget_device(matter_id)

        assert store.zigbee_pending.pending_for("7") == [(1, IAS_ZONE)]
    finally:
        store.close()

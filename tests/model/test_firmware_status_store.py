"""`firmware_status` rows (design 2026-09-30, section 8)."""

import json
from pathlib import Path

from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store
from loxmatter.sources.firmware import UpdateOffer

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"

# node 22 on the test Pi, measured 2026-09-30: KAJPLATS 1.1.0 -> 1.2.0
KAJPLATS_OFFER = UpdateOffer(
    software_version=16908288,
    software_version_string="1.2.0",
    min_applicable=0,
    max_applicable=16908287,
    release_notes_url=None,
    source="main-net-dcl",
)


def _store_with_lamp(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    raw = json.loads((FIXTURES / "ikea_kajplats_cws_lamp.json").read_text(encoding="utf-8"))
    device_id = store.register_device(NodeSnapshot.from_raw(raw["node_id"], raw))
    return store, device_id


def test_a_device_without_a_row_has_no_status(tmp_path):
    store, device_id = _store_with_lamp(tmp_path)
    assert store.firmware_status.get(device_id) is None
    assert store.firmware_status.all() == {}


def test_record_check_stores_the_offer(tmp_path):
    store, device_id = _store_with_lamp(tmp_path)
    store.firmware_status.record_check(device_id, KAJPLATS_OFFER, "2026-09-30T06:00:00+00:00")
    status = store.firmware_status.get(device_id)
    assert status is not None
    assert status.offer == KAJPLATS_OFFER
    assert status.checked_at == "2026-09-30T06:00:00+00:00"
    assert status.check_error is None


def test_record_check_without_offer_clears_the_old_one(tmp_path):
    store, device_id = _store_with_lamp(tmp_path)
    store.firmware_status.record_check(device_id, KAJPLATS_OFFER, "t1")
    store.firmware_status.record_check(device_id, None, "t2")
    status = store.firmware_status.get(device_id)
    assert status is not None and status.offer is None


def test_a_check_error_keeps_the_previous_offer(tmp_path):
    store, device_id = _store_with_lamp(tmp_path)
    store.firmware_status.record_check(device_id, KAJPLATS_OFFER, "t1")
    store.firmware_status.record_check_error(device_id, "no internet", "t2")
    status = store.firmware_status.get(device_id)
    assert status is not None
    assert status.offer == KAJPLATS_OFFER
    assert status.check_error == "no internet"
    assert status.checked_at == "t2"


def test_a_new_check_clears_a_failed_job_but_not_a_running_one(tmp_path):
    store, device_id = _store_with_lamp(tmp_path)
    store.firmware_status.end_job(device_id, "failed", "device refused", "t1")
    store.firmware_status.record_check(device_id, KAJPLATS_OFFER, "t2")
    status = store.firmware_status.get(device_id)
    assert status is not None and status.job_state is None and status.job_error is None

    store.firmware_status.start_job(device_id, "t3")
    store.firmware_status.record_check(device_id, KAJPLATS_OFFER, "t4")
    status = store.firmware_status.get(device_id)
    assert status is not None and status.job_state == "transferring"


def test_a_job_moves_through_its_states(tmp_path):
    store, device_id = _store_with_lamp(tmp_path)
    store.firmware_status.start_job(device_id, "t1")
    store.firmware_status.update_job(device_id, "transferring", 43, "t2")
    status = store.firmware_status.get(device_id)
    assert status is not None
    assert (status.job_state, status.job_progress, status.job_started_at) == (
        "transferring",
        43,
        "t1",
    )
    store.firmware_status.end_job(device_id, None, None, "t3")
    status = store.firmware_status.get(device_id)
    assert status is not None and status.job_state is None and status.job_progress is None


def test_drop_offer_keeps_the_check_time(tmp_path):
    store, device_id = _store_with_lamp(tmp_path)
    store.firmware_status.record_check(device_id, KAJPLATS_OFFER, "t1")
    store.firmware_status.drop_offer(device_id)
    status = store.firmware_status.get(device_id)
    assert status is not None and status.offer is None and status.checked_at == "t1"


def test_last_checked_at_is_the_newest_check(tmp_path):
    store, device_id = _store_with_lamp(tmp_path)
    assert store.firmware_status.last_checked_at() is None
    store.firmware_status.record_check(device_id, None, "2026-09-30T06:00:00+00:00")
    assert store.firmware_status.last_checked_at() == "2026-09-30T06:00:00+00:00"

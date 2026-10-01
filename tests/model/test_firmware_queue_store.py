"""The firmware update queue in the store (design 2026-10-01, section 3)."""

import sqlite3
import sys
from pathlib import Path

from loxmatter.model.store import Store

sys.path.insert(0, str(Path(__file__).parents[1] / "firmware"))
from firmware_fakes import register


def _store(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    a, _ = register(store, "ikea_kajplats_cws_lamp.json")
    b, _ = register(store, "ikea_bilresa_button.json")
    return store, a, b


def test_enqueue_orders_by_time_then_id(tmp_path):
    store, a, b = _store(tmp_path)
    assert store.firmware_status.enqueue([b, a], "2026-10-01T10:00:00Z") == 2
    assert store.firmware_status.queued() == sorted([a, b])
    assert store.firmware_status.get(a).queued_at == "2026-10-01T10:00:00Z"


def test_enqueue_keeps_an_earlier_place(tmp_path):
    store, a, b = _store(tmp_path)
    store.firmware_status.enqueue([b], "2026-10-01T10:00:00Z")
    assert store.firmware_status.enqueue([a, b], "2026-10-01T11:00:00Z") == 1
    assert store.firmware_status.queued() == [b, a]
    assert store.firmware_status.get(b).queued_at == "2026-10-01T10:00:00Z"


def test_dequeue_and_clear(tmp_path):
    store, a, b = _store(tmp_path)
    store.firmware_status.enqueue([a, b], "t")
    store.firmware_status.dequeue(a)
    assert store.firmware_status.queued() == [b]
    store.firmware_status.clear_queue()
    assert store.firmware_status.queued() == []


def test_a_forgotten_device_leaves_the_queue(tmp_path):
    store, a, b = _store(tmp_path)
    store.firmware_status.enqueue([a, b], "t")
    store.forget_device(a)
    assert store.firmware_status.queued() == [b]


def test_halt_reason_round_trip(tmp_path):
    store, _, _ = _store(tmp_path)
    key = "api.firmware.queue_halted_disconnected"
    assert store.firmware_settings.get_queue_halted_reason() is None
    store.firmware_settings.set_queue_halted_reason(key)
    assert store.firmware_settings.get_queue_halted_reason() == key
    store.firmware_settings.set_queue_halted_reason(None)
    assert store.firmware_settings.get_queue_halted_reason() is None


def test_a_v15_database_gains_queued_at(tmp_path):
    path = tmp_path / "v15.sqlite"
    store = Store(path)
    a, _ = register(store, "ikea_kajplats_cws_lamp.json")
    store.firmware_status.record_check(a, None, "t0")
    store.close()
    db = sqlite3.connect(str(path))
    db.executescript(
        "ALTER TABLE firmware_status RENAME TO fs_v16;"
        " CREATE TABLE firmware_status (device_id INTEGER PRIMARY KEY, checked_at TEXT,"
        " check_error TEXT, offer_version INTEGER, offer_version_string TEXT,"
        " offer_min_applicable INTEGER, offer_max_applicable INTEGER, offer_notes_url TEXT,"
        " offer_source TEXT, job_state TEXT, job_progress INTEGER, job_started_at TEXT,"
        " job_changed_at TEXT, job_error TEXT);"
        " INSERT INTO firmware_status SELECT device_id, checked_at, check_error, offer_version,"
        " offer_version_string, offer_min_applicable, offer_max_applicable, offer_notes_url,"
        " offer_source, job_state, job_progress, job_started_at, job_changed_at, job_error"
        " FROM fs_v16;"
        " DROP TABLE fs_v16;"
        " PRAGMA user_version = 15;"
    )
    db.commit()
    db.close()
    store = Store(path)
    assert store.firmware_status.get(a).checked_at == "t0"
    assert store.firmware_status.get(a).queued_at is None
    assert store.firmware_status.enqueue([a], "t1") == 1

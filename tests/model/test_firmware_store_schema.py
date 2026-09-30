"""Schema 15 (design 2026-09-30, sections 8 and 9.3): additive only."""

import json
import sqlite3
from pathlib import Path

from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store, schema_version

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def _snapshot(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def _columns(path: Path, table: str) -> set[str]:
    db = sqlite3.connect(str(path))
    try:
        return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
    finally:
        db.close()


def test_schema_version_is_15():
    assert schema_version() == 15


def test_register_device_stores_the_spec_version(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    device_id = store.register_device(_snapshot("ikea_kajplats_cws_lamp.json"))
    assert store.device(device_id).matter_spec_version == 0x01040000


def test_a_v14_database_gains_the_column_and_the_table(tmp_path):
    path = tmp_path / "v14.sqlite"
    store = Store(path)
    device_id = store.register_device(_snapshot("ikea_bilresa_button.json"))
    store.close()
    db = sqlite3.connect(str(path))
    db.executescript(
        "DROP TABLE firmware_status;"
        " CREATE TABLE device_old AS SELECT id, unique_id, node_id, technology, address,"
        " label, udp_port, active, exported_at, updated_at, room, device_types,"
        " network_features, vendor_name, product_name, firmware, serial_number FROM device;"
        " DROP TABLE device;"
        " ALTER TABLE device_old RENAME TO device;"
        " PRAGMA user_version = 14;"
    )
    db.commit()
    db.close()
    assert "matter_spec_version" not in _columns(path, "device")

    store = Store(path)
    assert "matter_spec_version" in _columns(path, "device")
    assert "job_state" in _columns(path, "firmware_status")
    assert store.device(device_id).matter_spec_version is None
    assert store.backfill_matter_spec_version([_snapshot("ikea_bilresa_button.json")]) == 1
    assert store.device(device_id).matter_spec_version == 0x01030000


def test_backfill_never_overwrites_a_known_value(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    snapshot = _snapshot("ikea_kajplats_cws_lamp.json")
    device_id = store.register_device(snapshot)
    assert store.backfill_matter_spec_version([snapshot]) == 0
    assert store.device(device_id).matter_spec_version == 0x01040000


def test_set_firmware_details_overwrites_both_values(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    device_id = store.register_device(_snapshot("ikea_kajplats_cws_lamp.json"))
    store.set_firmware_details(device_id, "1.3.0", 0x01050000)
    device = store.device(device_id)
    assert device.firmware == "1.3.0"
    assert device.matter_spec_version == 0x01050000

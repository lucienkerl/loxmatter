"""DCL answers kept in the store (design 2026-10-02, section 7)."""

import sqlite3

from loxmatter.matter.dcl import DclModel, DclVendor
from loxmatter.model.store import Store, schema_version


def test_schema_is_17():
    assert schema_version() == 17


def test_vendor_and_model_round_trip(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    store.dcl.put_vendor(4476, DclVendor(4476, "IKEA of Sweden"), "2026-10-02T10:00:00+00:00")
    model = DclModel(4476, 36865, "KAJPLATS E27 WS globe 1055lm", "LED2407G8", 268, 1, None)
    store.dcl.put_model(4476, 36865, model, "2026-10-02T10:00:00+00:00")
    assert store.dcl.vendor(4476).entry == DclVendor(4476, "IKEA of Sweden")
    assert store.dcl.model(4476, 36865).entry == model


def test_not_in_the_dcl_is_remembered(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    store.dcl.put_model(4476, 1, None, "2026-10-02T10:00:00+00:00")
    cached = store.dcl.model(4476, 1)
    assert cached is not None and cached.entry is None
    assert store.dcl.model(4476, 2) is None


def test_a_v16_database_gains_the_tables(tmp_path):
    path = tmp_path / "v16.sqlite"
    Store(path).close()
    db = sqlite3.connect(str(path))
    db.executescript("DROP TABLE dcl_vendor; DROP TABLE dcl_model; PRAGMA user_version = 16;")
    db.close()
    store = Store(path)
    store.dcl.put_vendor(4476, DclVendor(4476, "IKEA of Sweden"), "t")
    assert store.dcl.vendor(4476) is not None

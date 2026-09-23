"""Tests for the "last saved" stamp of a project file - see
`projectsync/savedate.py` and where `patch`/`sync` apply it."""

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from loxmatter.export.commands import extract_commands
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store
from loxmatter.projectsync.savedate import recorded_utc_offset, saved_date_attrs
from loxmatter.projectsync.sync import run_sync

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"

# Pairs copied from real project files saved by Loxone Config: one in
# summer (CEST), one in winter (CET), and one from a computer set to UTC.
SUMMER = {"Date": "2026-09-23 23:13:53", "DateS": "559430033"}
WINTER = {"Date": "2025-01-29 17:03:42", "DateS": "507398622"}
UTC_MACHINE = {"Date": "2016-11-24 17:30:46", "DateS": "249240646"}

CEST = timezone(timedelta(hours=2))


def _plug_store(tmp_path):
    """The same store as `test_sync._plug_store`: one plug, which the
    sample project lacks a signal for, so the plan always changes the
    file. Local for the reason `test_sync.load_snapshot` gives."""
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    snapshot = NodeSnapshot.from_raw(raw["node_id"], raw)
    store = Store(tmp_path / "t.sqlite")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot))
    return store


@pytest.mark.parametrize(
    ("attrs", "hours"),
    [(SUMMER, 2), (WINTER, 1), (UTC_MACHINE, 0)],
)
def test_recorded_offset_is_read_from_real_files(attrs, hours):
    assert recorded_utc_offset(attrs) == timedelta(hours=hours)


@pytest.mark.parametrize(
    "attrs",
    [
        {},
        {"Date": "2026-09-23 23:13:53"},
        {"DateS": "559430033"},
        {"Date": "", "DateS": "559430033"},
        {"Date": "2026-09-23 23:13:53", "DateS": "abc"},
        # A day apart: not a zone, two moments.
        {"Date": "2026-09-24 23:13:53", "DateS": "559430033"},
    ],
)
def test_recorded_offset_is_none_when_it_cannot_be_read(attrs):
    assert recorded_utc_offset(attrs) is None


def test_saved_date_attrs_reproduce_what_loxone_config_wrote():
    saved_at = datetime(2026, 9, 23, 23, 13, 53, tzinfo=CEST)
    assert saved_date_attrs(saved_at) == SUMMER


def test_saved_date_attrs_refuse_a_naive_moment():
    with pytest.raises(ValueError, match="time zone"):
        saved_date_attrs(datetime(2026, 9, 23, 23, 13, 53))  # noqa: DTZ001 - the point


def _stamped(project: str, attrs: dict[str, str]) -> str:
    """`project` with `Date`/`DateS` on its document, as Loxone Config
    writes them: after `Title`, among the document's other attributes."""
    return project.replace(
        'Title="Testprojekt">',
        f'Title="Testprojekt" Date="{attrs["Date"]}" CDate="" BDate=""'
        f' DateS="{attrs["DateS"]}" Street="">',
        1,
    )


def test_a_changed_file_is_stamped_with_the_users_time(tmp_path, sample_project):
    store = _plug_store(tmp_path)
    now = datetime(2026, 10, 1, 8, 30, 0, tzinfo=UTC)
    result = run_sync(
        _stamped(sample_project, WINTER).encode("utf-8"),
        store,
        bridge_ip="10.0.0.5",
        port=7000,
        listen=8080,
        utc_offset=timedelta(hours=2),
        now=now,
    )
    assert result.plan.has_changes
    expected = saved_date_attrs(now.astimezone(CEST))
    assert expected["Date"] == "2026-10-01 10:30:00"
    assert (
        f'Title="Testprojekt" Date="{expected["Date"]}" CDate="" BDate=""'
        f' DateS="{expected["DateS"]}" Street="">'
    ).encode() in result.patched
    store.close()


def test_without_an_offset_the_files_own_is_used(tmp_path, sample_project):
    store = _plug_store(tmp_path)
    now = datetime(2026, 10, 1, 8, 30, 0, tzinfo=UTC)
    result = run_sync(
        _stamped(sample_project, SUMMER).encode("utf-8"),
        store,
        bridge_ip="10.0.0.5",
        port=7000,
        listen=8080,
        now=now,
    )
    assert b'Date="2026-10-01 10:30:00"' in result.patched
    store.close()


def test_an_unchanged_file_keeps_its_stamp(tmp_path, sample_project):
    store = Store(tmp_path / "empty.sqlite")
    project = _stamped(sample_project, SUMMER)
    result = run_sync(
        project.encode("utf-8"),
        store,
        bridge_ip="10.0.0.5",
        port=7000,
        listen=8080,
        utc_offset=timedelta(hours=2),
        now=datetime(2026, 10, 1, 8, 30, 0, tzinfo=UTC),
    )
    assert not result.plan.has_changes
    assert f'Date="{SUMMER["Date"]}"'.encode() in result.patched
    assert f'DateS="{SUMMER["DateS"]}"'.encode() in result.patched
    store.close()


def test_a_file_without_a_stamp_does_not_get_one(tmp_path, sample_project):
    store = _plug_store(tmp_path)
    result = run_sync(
        sample_project.encode("utf-8"),
        store,
        bridge_ip="10.0.0.5",
        port=7000,
        listen=8080,
        utc_offset=timedelta(hours=2),
    )
    assert result.plan.has_changes
    assert b"Date=" not in result.patched
    assert b"DateS=" not in result.patched
    store.close()

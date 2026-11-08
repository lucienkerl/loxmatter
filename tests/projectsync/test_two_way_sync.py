"""The two-way project sync (design 2026-11-08): room and name differences
in both directions, rooms of new signals, and deleting orphaned objects
together with their wiring."""

import json
import re
from pathlib import Path

from room_fixtures import with_rooms

from loxmatter.export.commands import extract_commands
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store
from loxmatter.model.sync_baseline_store import OwnerBaseline, SyncBaseline
from loxmatter.projectsync.reconcile import SyncDecisions
from loxmatter.projectsync.sync import apply_decisions_to_store, run_sync

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"

# From `room_fixtures.ROOMS_XML`: the default room and "Küche".
DEFAULT_ROOM_U = "3000-0001-0000-aaaaaaaaaaaaaaaa"
KITCHEN_U = "3000-0002-0000-aaaaaaaaaaaaaaaa"
# The `Pr` every object of `SAMPLE_PROJECT` carries.
SAMPLE_PR = "1000-0006-0000-aaaaaaaaaaaaaaaa"


def _plug_store(tmp_path: Path) -> Store:
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    snapshot = NodeSnapshot.from_raw(raw["node_id"], raw)
    store = Store(tmp_path / "t.sqlite")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot))
    return store


def _sync(project: str, store: Store, decisions: SyncDecisions | None = None):
    return run_sync(
        project.encode("utf-8"),
        store,
        bridge_ip="10.0.0.9",
        port=7000,
        listen=8080,
        decisions=decisions,
    )


def _in_kitchen(sample_project: str) -> str:
    return with_rooms(sample_project.replace(SAMPLE_PR, KITCHEN_U))


def _orphan(result, prefix: str):
    return next(o for o in result.reconciliation.orphans if o.prefix == prefix)


def _pr_of(patched: bytes, check: str) -> str:
    text = patched.decode("utf-8-sig")
    start = text.index(check)
    iodata = text.index("<IoData", start)
    match = re.search(r'Pr="([^"]*)"', text[iodata : text.index("/>", iodata)])
    assert match is not None
    return match.group(1)


def test_a_room_set_in_loxone_is_a_difference_loxone_is_preselected_for(tmp_path, sample_project):
    store = _plug_store(tmp_path)
    result = _sync(_in_kitchen(sample_project), store)
    [difference] = result.reconciliation.rooms
    assert difference.owner.ref == "device:1"
    assert difference.loxone == "Küche"
    assert difference.owner.room is None
    assert difference.preselect == "loxone"
    store.close()


def test_choosing_loxone_stores_the_room_in_loxmatter(tmp_path, sample_project):
    store = _plug_store(tmp_path)
    result = _sync(_in_kitchen(sample_project), store, SyncDecisions(rooms={"device:1": "loxone"}))
    apply_decisions_to_store(store, result)
    assert store.device(1).room == "Küche"
    assert store.sync_baseline.get().owners["device:1"].room == "Küche"
    store.close()


def test_without_apply_nothing_is_stored(tmp_path, sample_project):
    store = _plug_store(tmp_path)
    _sync(_in_kitchen(sample_project), store, SyncDecisions(rooms={"device:1": "loxone"}))
    assert store.device(1).room is None
    assert store.sync_baseline.get().owners == {}
    store.close()


def test_choosing_loxmatter_moves_the_existing_objects_into_its_room(tmp_path, sample_project):
    store = _plug_store(tmp_path)
    store.set_room(1, "Wohnzimmer")
    result = _sync(
        _in_kitchen(sample_project), store, SyncDecisions(rooms={"device:1": "loxmatter"})
    )
    patched = result.patched.decode("utf-8-sig")
    living = re.search(r'Type="Place"[^>]*U="([^"]*)" Title="Wohnzimmer"', patched)
    assert living is not None, "the room is created"
    assert _pr_of(result.patched, 'Check="d1_1_onoff') == living.group(1)
    assert _pr_of(result.patched, 'CmdOn="/cmd/d1_1_on/1"') == living.group(1)
    # The orphan of another owner keeps its room.
    assert _pr_of(result.patched, 'Check="d9_9_verwaist') == KITCHEN_U
    store.close()


def test_skipping_leaves_both_sides_alone(tmp_path, sample_project):
    store = _plug_store(tmp_path)
    store.set_room(1, "Wohnzimmer")
    result = _sync(_in_kitchen(sample_project), store)
    assert _pr_of(result.patched, 'Check="d1_1_onoff') == KITCHEN_U
    apply_decisions_to_store(store, result)
    assert store.device(1).room == "Wohnzimmer"
    # A skipped owner gets no baseline, so the next sync still asks.
    assert store.sync_baseline.get().owners["device:1"].room is None
    store.close()


def test_a_new_signal_joins_the_room_its_siblings_share(tmp_path, sample_project):
    store = _plug_store(tmp_path)
    store.set_room(1, "Wohnzimmer")
    result = _sync(_in_kitchen(sample_project), store)
    assert _pr_of(result.patched, 'Check="d1_2_power') == KITCHEN_U
    store.close()


def test_objects_split_over_rooms_are_no_difference(tmp_path, sample_project):
    store = _plug_store(tmp_path)
    store.set_room(1, "Wohnzimmer")
    # Only the on/off input moves to the kitchen; the rest stays in the
    # default room.
    project = with_rooms(sample_project.replace(SAMPLE_PR, DEFAULT_ROOM_U))
    onoff = project.index('Check="d1_1_onoff')
    head, tail = project[:onoff], project[onoff:]
    project = head + tail.replace(DEFAULT_ROOM_U, KITCHEN_U, 1)

    result = _sync(project, store)
    assert result.reconciliation.rooms == []
    [split] = result.reconciliation.split
    assert split.rooms == ["", "Küche"]
    # New signals of a split owner go to loxmatter's room.
    living = re.search(r'U="([^"]*)" Title="Wohnzimmer"', result.patched.decode("utf-8-sig"))
    assert living is not None
    assert _pr_of(result.patched, 'Check="d1_2_power') == living.group(1)
    store.close()


def test_the_baseline_tells_which_side_moved(tmp_path, sample_project):
    store = _plug_store(tmp_path)
    store.set_room(1, "Wohnzimmer")
    store.sync_baseline.set(SyncBaseline({"device:1": OwnerBaseline(room="Küche", name=None)}))
    [difference] = _sync(_in_kitchen(sample_project), store).reconciliation.rooms
    # Loxone still shows the agreed room: loxmatter is the one that moved.
    assert difference.preselect == "loxmatter"
    store.close()


def test_a_container_renamed_in_loxone_is_a_name_difference(tmp_path, sample_project):
    store = _plug_store(tmp_path)
    label = store.device(1).label
    result = _sync(sample_project, store)
    [name] = result.reconciliation.names
    # The name inside loxmatter's "Matter — …" wrapping.
    assert name.loxone == "Altes Geraet"
    assert name.owner.label == label
    store.close()


def test_choosing_loxmatter_renames_the_containers(tmp_path, sample_project):
    store = _plug_store(tmp_path)
    label = store.device(1).label
    result = _sync(sample_project, store, SyncDecisions(names={"device:1": "loxmatter"}))
    patched = result.patched.decode("utf-8-sig")
    assert 'Altes Geraet"' not in patched
    # Input and output container, each keeping loxmatter's wrapping.
    assert patched.count(f'Title="Matter — {label}"') == 2
    store.close()


def test_a_container_loxmatter_created_is_no_name_difference(tmp_path, sample_project):
    store = _plug_store(tmp_path)
    store.rename_device(1, "Altes Geraet")
    assert _sync(sample_project, store).reconciliation.names == []
    store.close()


def test_a_plain_title_stays_plain_when_renamed(tmp_path, sample_project):
    store = _plug_store(tmp_path)
    project = sample_project.replace("Matter — Altes Geraet", "Grill")
    result = _sync(project, store, SyncDecisions(names={"device:1": "loxmatter"}))
    label = store.device(1).label
    assert result.patched.decode("utf-8-sig").count(f'Title="{label}"') == 2
    store.close()


def test_choosing_the_loxone_name_renames_the_device(tmp_path, sample_project):
    store = _plug_store(tmp_path)
    result = _sync(sample_project, store, SyncDecisions(names={"device:1": "loxone"}))
    apply_decisions_to_store(store, result)
    assert store.device(1).label == "Altes Geraet"
    store.close()


def test_an_orphan_of_a_removed_device_is_offered_for_deletion(tmp_path, sample_project):
    store = _plug_store(tmp_path)
    orphan = _orphan(_sync(sample_project, store), "d9")
    assert orphan.reason == "removed"
    assert orphan.delete_by_default
    assert orphan.links == []
    store.close()


def test_deleting_an_orphan_removes_its_object_and_keeps_the_container(tmp_path, sample_project):
    store = _plug_store(tmp_path)
    result = _sync(sample_project, store, SyncDecisions(delete=frozenset({"d9"})))
    patched = result.patched.decode("utf-8-sig")
    assert "d9_9_verwaist" not in patched
    assert 'Check="d1_online' in patched
    # The line went with it: no blank, indented line is left behind.
    assert not re.search(r"\n[\t ]*\r?\n", patched)
    store.close()


def test_without_deletion_the_orphan_stays(tmp_path, sample_project):
    store = _plug_store(tmp_path)
    result = _sync(sample_project, store)
    assert b"d9_9_verwaist" in result.patched
    store.close()


# A program page wiring the orphan "d9_9_verwaist" (`U` ...0007, output
# connector `Q` ...0009) through an `InputRef` into a function block,
# whose input also takes a second, unrelated link.
_PAGE = (
    '\t\t<C Type="Program" U="4000-0000-0000-aaaaaaaaaaaaaaaa" Title="Programm">\r\n'
    '\t\t\t<C Type="Page" U="4000-0001-0000-aaaaaaaaaaaaaaaa" Title="Wohnzimmer">\r\n'
    '\t\t\t\t<C Type="InputRef" U="4000-0002-0000-aaaaaaaaaaaaaaaa" Title="Verwaist"'
    ' Ref="1000-0007-0000-aaaaaaaaaaaaaaaa" LinkRefType="79">\r\n'
    '\t\t\t\t\t<Co K="I" Nc="1" U="4000-0003-0000-bbbbbbbbbbbbbbbb">'
    '<In Input="1000-0009-0000-bbbbbbbbbbbbbbbb"/></Co>\r\n'
    '\t\t\t\t\t<Co K="Q" U="4000-0004-0000-bbbbbbbbbbbbbbbb"/>\r\n'
    "\t\t\t\t</C>\r\n"
    '\t\t\t\t<C Type="Or" U="4000-0005-0000-aaaaaaaaaaaaaaaa" Title="Oder">\r\n'
    '\t\t\t\t\t<Co K="I1" Nc="2" U="4000-0006-0000-bbbbbbbbbbbbbbbb">'
    '<In Input="4000-0004-0000-bbbbbbbbbbbbbbbb"/>'
    '<In Input="9999-0000-0000-bbbbbbbbbbbbbbbb" FLG="2"/></Co>\r\n'
    '\t\t\t\t\t<Co K="I2" Nc="1" U="4000-0007-0000-bbbbbbbbbbbbbbbb">'
    '<In Input="4000-0004-0000-bbbbbbbbbbbbbbbb"/></Co>\r\n'
    "\t\t\t\t</C>\r\n"
    "\t\t\t</C>\r\n"
    "\t\t</C>\r\n"
)


def _with_page(project: str) -> str:
    return project.replace("\t</C>\r\n</ControlList>", _PAGE + "\t</C>\r\n</ControlList>")


def test_an_orphan_lists_the_pages_it_is_wired_on(tmp_path, sample_project):
    store = _plug_store(tmp_path)
    orphan = _orphan(_sync(_with_page(sample_project), store), "d9")
    assert [(link.title, link.page) for link in orphan.links] == [("Verwaist", "Wohnzimmer")]
    store.close()


def test_deleting_a_wired_orphan_removes_its_wiring(tmp_path, sample_project):
    store = _plug_store(tmp_path)
    result = _sync(_with_page(sample_project), store, SyncDecisions(delete=frozenset({"d9"})))
    patched = result.patched.decode("utf-8-sig")
    assert 'Type="InputRef"' not in patched
    assert "4000-0004-0000-bbbbbbbbbbbbbbbb" not in patched
    # The function block stays; one link left keeps its `Nc` in step, none
    # left makes the connector self-closing without one.
    assert (
        '<Co K="I1" Nc="1" U="4000-0006-0000-bbbbbbbbbbbbbbbb">'
        '<In Input="9999-0000-0000-bbbbbbbbbbbbbbbb" FLG="2"/></Co>'
    ) in patched
    assert '<Co K="I2" U="4000-0007-0000-bbbbbbbbbbbbbbbb"/>' in patched
    store.close()


def test_an_orphan_with_its_own_container_takes_the_container_along(tmp_path, sample_project):
    # Move the orphan into a container of its own.
    project = sample_project.replace(
        '\t\t\t\t\t<C Type="VirtualUdpInCmd" IName="VCI2"',
        '\t\t\t\t</C>\r\n\t\t\t\t<C Type="VirtualUdpIn" IName="VUI9"'
        ' U="1000-0011-0000-aaaaaaaaaaaaaaaa" Title="TIMMERFLOTTE" Address="10.0.0.9"'
        ' Port="7000">\r\n\t\t\t\t\t<C Type="VirtualUdpInCmd" IName="VCI2"',
    )
    store = _plug_store(tmp_path)
    orphan = _orphan(_sync(project, store), "d9")
    assert orphan.label == "TIMMERFLOTTE"
    result = _sync(project, store, SyncDecisions(delete=frozenset({"d9"})))
    patched = result.patched.decode("utf-8-sig")
    assert "TIMMERFLOTTE" not in patched
    assert 'Check="d1_online' in patched
    store.close()


def test_kept_orphans_are_offered_unticked_next_time(tmp_path, sample_project):
    store = _plug_store(tmp_path)
    apply_decisions_to_store(store, _sync(sample_project, store))
    assert not _orphan(_sync(sample_project, store), "d9").delete_by_default
    store.close()


def test_a_group_member_left_in_loxone_is_a_via_group_orphan(tmp_path, sample_project):
    store = _plug_store(tmp_path)
    store.create_group("Deckenlicht", [1])
    result = _sync(sample_project, store)
    reasons = {o.prefix: o.reason for o in result.reconciliation.orphans}
    assert reasons["d1"] == "via_group"
    [member] = [o for o in result.reconciliation.orphans if o.prefix == "d1"]
    assert member.group_labels == ["Deckenlicht"]
    store.close()

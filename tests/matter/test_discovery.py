from loxmatter.matter.discovery import (
    extract_signals,
    find_unparsable_paths,
    find_unreported_attributes,
)
from loxmatter.matter.models import NodeSnapshot, SignalKind, SignalRef


def snapshot(attributes: dict[str, object]) -> NodeSnapshot:
    return NodeSnapshot.from_raw(node_id=1, raw={"attributes": attributes})


def test_every_non_global_attribute_becomes_a_signal():
    signals = extract_signals(snapshot({"1/6/0": True, "1/8/0": 254}))
    assert signals == [
        SignalRef(1, 6, 0, SignalKind.ATTRIBUTE),
        SignalRef(1, 8, 0, SignalKind.ATTRIBUTE),
    ]


def test_global_attributes_are_not_signals():
    signals = extract_signals(
        snapshot({"1/6/0": True, "1/6/65533": 6, "1/6/65532": 0, "1/6/65531": [0]})
    )
    assert signals == [SignalRef(1, 6, 0, SignalKind.ATTRIBUTE)]


def test_event_list_produces_event_signals():
    signals = extract_signals(snapshot({"1/59/65530": [0, 1, 2]}))
    assert signals == [
        SignalRef(1, 59, 0, SignalKind.EVENT),
        SignalRef(1, 59, 1, SignalKind.EVENT),
        SignalRef(1, 59, 2, SignalKind.EVENT),
    ]


def test_empty_or_absent_event_list_produces_nothing():
    assert extract_signals(snapshot({"1/59/65530": []})) == []
    assert extract_signals(snapshot({"1/59/65530": None})) == []


def test_unknown_cluster_is_still_extracted():
    """Spec 3.5: profiles/ ist Anreicherung, kein Gatekeeper."""
    signals = extract_signals(snapshot({"1/64999/7": 42}))
    assert signals == [SignalRef(1, 64999, 7, SignalKind.ATTRIBUTE)]


def test_signals_are_sorted_deterministically():
    signals = extract_signals(snapshot({"2/6/0": True, "1/1030/0": 1, "1/6/0": False}))
    assert [s.path for s in signals] == ["1/6/0", "1/1030/0", "2/6/0"]


def test_finds_attributes_the_device_claims_but_did_not_report():
    # AttributeList (65531) nennt 0 und 16, geliefert wurde nur 0.
    missing = find_unreported_attributes(snapshot({"1/6/65531": [0, 16], "1/6/0": True}))
    assert missing == [SignalRef(1, 6, 16, SignalKind.ATTRIBUTE)]


def test_reports_nothing_missing_when_device_is_complete():
    assert find_unreported_attributes(snapshot({"1/6/65531": [0], "1/6/0": True})) == []


def test_global_attributes_are_not_counted_as_missing():
    missing = find_unreported_attributes(snapshot({"1/6/65531": [0, 65533], "1/6/0": True}))
    assert missing == []


def test_unparsable_paths_are_collected_not_raised():
    snap = snapshot({"kaputt": 1, "1/6/0": True})
    assert find_unparsable_paths(snap) == ["kaputt"]
    assert extract_signals(snap) == [SignalRef(1, 6, 0, SignalKind.ATTRIBUTE)]

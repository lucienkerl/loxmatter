import pytest

from loxmatter.projectsync.ids import new_iname, new_unique_id


def test_new_unique_id_reuses_installation_suffix_from_an_existing_id():
    existing = {"1000-0001-0000-aaaaaaaaaaaaaaaa"}
    new_id = new_unique_id(existing)
    assert new_id.endswith("-aaaaaaaaaaaaaaaa")
    assert new_id in existing  # als vergeben markiert


def test_new_unique_id_never_collides_across_many_calls():
    existing = {"1000-0001-0000-aaaaaaaaaaaaaaaa"}
    generated = {new_unique_id(existing) for _ in range(500)}
    assert len(generated) == 500  # keine Kollision, keine ID doppelt


def test_new_unique_id_raises_without_any_reference_id():
    with pytest.raises(ValueError):
        new_unique_id(set())


def test_new_iname_finds_next_free_number_skipping_gaps():
    existing = {"VCI1", "VCI3", "VCI4"}
    name = new_iname("VCI", existing)
    assert name == "VCI2"
    assert "VCI2" in existing


def test_new_iname_starts_at_one_for_an_unused_prefix():
    existing: set[str] = set()
    assert new_iname("VQC", existing) == "VQC1"

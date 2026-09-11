import pytest

from spellbook_builder.store import RuntimeStore


def record(spell_id: str, memberships: list[dict]) -> dict:
    return {
        "id": spell_id,
        "name": spell_id,
        "content_blocks": [{"type": "paragraph", "text": "fixture"}],
        "imported_from_classes": memberships,
    }


def test_startup_removes_legacy_incomplete_import_data_and_temps(tmp_path):
    runtime = tmp_path / "runtime"
    store = RuntimeStore(runtime)
    state = store.load_state()
    state["imported_classes"]["Completed"] = {
        "class_name": "Completed",
        "levels": {"0": ["completed"]},
    }
    state["spellbooks"]["fixture"] = {
        "open_batch": {"entities": [{"kind": "spell", "id": "book-reference"}]},
        "batches": [],
    }
    store.save_state(state)
    store.save_spells(
        {
            "completed": record(
                "completed",
                [
                    {"class_name": "Completed", "spell_level": 0},
                    {"class_name": "Interrupted", "spell_level": 1},
                ],
            ),
            "book-reference": record(
                "book-reference", [{"class_name": "Interrupted", "spell_level": 1}]
            ),
            "orphan": record("orphan", [{"class_name": "Interrupted", "spell_level": 1}]),
            "standalone": record("standalone", []),
        }
    )
    state_temp = runtime / ".state.json.abandoned.tmp"
    spells_temp = runtime / "indexes" / ".spells.json.abandoned.tmp"
    state_temp.write_bytes(b"unused")
    spells_temp.write_bytes(b"unused")

    reopened = RuntimeStore(runtime)

    spells = reopened.load_spells()
    assert set(spells) == {"completed", "book-reference", "standalone"}
    assert spells["completed"]["imported_from_classes"] == [
        {"class_name": "Completed", "spell_level": 0}
    ]
    assert spells["book-reference"]["imported_from_classes"] == []
    assert reopened.load_state()["index_metadata"]["spells"]["record_count"] == 3
    assert not state_temp.exists()
    assert not spells_temp.exists()


def test_failed_class_import_commit_restores_previous_spell_index(monkeypatch, tmp_path):
    store = RuntimeStore(tmp_path / "runtime")
    existing = record("existing", [])
    store.save_spells({"existing": existing})
    class_record = {"class_name": "Fixture", "levels": {"0": ["incoming"]}}
    incoming = record("incoming", [{"class_name": "Fixture", "spell_level": 0}])

    def fail_state_write(_state):
        raise OSError("disk full")

    monkeypatch.setattr(store, "save_state", fail_state_write)

    with pytest.raises(OSError, match="disk full"):
        store.merge_class_import(class_record, {"incoming": incoming})

    assert store.load_spells() == {"existing": existing}


def test_reimport_removes_a_now_missing_unreferenced_spell(tmp_path):
    store = RuntimeStore(tmp_path / "runtime")
    old_class = {"class_name": "Fixture", "levels": {"0": ["old-spell"]}}
    old_spell = record("old-spell", [{"class_name": "Fixture", "spell_level": 0}])
    store.merge_class_import(old_class, {"old-spell": old_spell})

    store.merge_class_import({"class_name": "Fixture", "levels": {"0": []}}, {})

    assert store.load_spells() == {}

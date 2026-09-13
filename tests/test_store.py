import pytest

from spellbook_builder import store as store_module
from spellbook_builder.arkal import spell_id_from_name
from spellbook_builder.store import RuntimeStore, StoreError
from spellbook_builder.util import atomic_json_write


def record(spell_id: str, memberships: list[dict]) -> dict:
    return {
        "id": spell_id,
        "name": spell_id,
        "content_blocks": [{"type": "paragraph", "text": "fixture"}],
        "imported_from_classes": memberships,
    }


def printing(spell_id: str, name: str, book: str, memberships: list[dict]) -> dict:
    return {**record(spell_id, memberships), "name": name, "source_book": book}


def wizard(level: int) -> list[dict]:
    return [{"class_name": "Wizard", "spell_level": level}]


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
    # Stale memberships stay so the record is reclaimed when the book goes.
    assert spells["book-reference"]["imported_from_classes"] == [
        {"class_name": "Interrupted", "spell_level": 1}
    ]
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


def test_deleting_a_class_keeps_spells_still_listed_or_referenced(tmp_path):
    store = RuntimeStore(tmp_path / "runtime")
    wizard = [{"class_name": "Wizard", "spell_level": 1}]
    store.merge_class_import(
        {"class_name": "Wizard", "levels": {"1": ["shared", "wizard-only", "in-book"]}},
        {
            "shared": record("shared", wizard),
            "wizard-only": record("wizard-only", wizard),
            "in-book": record("in-book", wizard),
        },
    )
    store.merge_class_import(
        {"class_name": "Cleric", "levels": {"2": ["shared"]}},
        {"shared": record("shared", [{"class_name": "Cleric", "spell_level": 2}])},
    )
    book = store.create_spellbook("Grimoire")
    store.begin_batch(book["id"])
    store.add_open_entity(book["id"], {"kind": "spell", "id": "in-book"})

    assert store.delete_imported_class("Wizard") == 1

    state = store.load_state()
    assert set(state["imported_classes"]) == {"Cleric"}
    assert state["index_metadata"]["spells"]["record_count"] == 2
    spells = store.load_spells()
    assert set(spells) == {"shared", "in-book"}
    assert spells["shared"]["imported_from_classes"] == [{"class_name": "Cleric", "spell_level": 2}]

    store.delete_spellbook(book["id"])

    assert set(store.load_spells()) == {"shared"}


def test_class_imports_keep_the_preferred_printing_of_a_shared_spell(tmp_path):
    store = RuntimeStore(tmp_path / "runtime")
    spell_id = spell_id_from_name("Acid Splash")
    store.merge_class_import(
        {"class_name": "Wizard", "levels": {"0": [spell_id]}},
        {spell_id: printing(spell_id, "Acid Splash", "Player's Handbook v.3.5", wizard(0))},
    )
    store.merge_class_import(
        {"class_name": "Sorcerer", "levels": {"0": [spell_id]}},
        {spell_id: printing(spell_id, "Acid Splash", "Magic of Faerun", [{"class_name": "Sorcerer", "spell_level": 0}])},
    )

    kept = store.load_spells()[spell_id]
    assert kept["source_book"] == "Player's Handbook v.3.5"
    assert kept["imported_from_classes"] == [*wizard(0), {"class_name": "Sorcerer", "spell_level": 0}]


# Migration writes: spells with old records kept, state, spells without them.
@pytest.mark.parametrize("killed_write", [None, 2, 3])
def test_startup_merges_printings_imported_under_url_ids(monkeypatch, tmp_path, killed_write):
    runtime = tmp_path / "runtime"
    store = RuntimeStore(runtime)
    mof_acid = "arkal-magic-of-faerun-20-acid-splash-1604"
    phb_acid = "arkal-players-handbook-v35-6-acid-splash-2373"
    light = "arkal-players-handbook-v35-6-light-2601"
    bovd_thief = "arkal-book-of-vile-darkness-3-phantasmal-thief-310"
    spc_thief = "arkal-spell-compendium-86-phantasmal-thief-4217"
    store.save_spells(
        {
            mof_acid: printing(mof_acid, "Acid Splash", "Magic of Faerun", wizard(0)),
            phb_acid: printing(phb_acid, "Acid Splash", "Player's Handbook v.3.5", wizard(0)),
            light: printing(light, "Light", "Player's Handbook v.3.5", wizard(0)),
            bovd_thief: printing(bovd_thief, "Phantasmal Thief", "Book of Vile Darkness", wizard(8)),
            spc_thief: printing(spc_thief, "Phantasmal Thief", "Spell Compendium", wizard(5)),
        }
    )
    state = store.load_state()
    state["imported_classes"]["Wizard"] = {
        "class_name": "Wizard",
        "levels": {"0": [mof_acid, phb_acid, light], "5": [spc_thief], "8": [bovd_thief]},
    }
    state["spellbooks"]["grimoire"] = {
        "batches": [{"entities": [{"kind": "spell", "id": mof_acid, "title": "Acid Splash", "page": 1}]}],
        "open_batch": {"entities": [{"kind": "spell", "id": mof_acid}, {"kind": "spell", "id": phb_acid}]},
    }
    store.save_state(state)
    if killed_write:
        writes = 0

        def write_until_killed(path, value):
            nonlocal writes
            writes += 1
            if writes == killed_write:
                raise OSError("killed")
            atomic_json_write(path, value)

        monkeypatch.setattr(store_module, "atomic_json_write", write_until_killed)
        with pytest.raises(OSError, match="killed"):
            RuntimeStore(runtime)
        monkeypatch.undo()

    reopened = RuntimeStore(runtime)

    spells = reopened.load_spells()
    assert set(spells) == {"arkal-acid-splash", "arkal-light", "arkal-phantasmal-thief"}
    assert spells["arkal-acid-splash"]["source_book"] == "Player's Handbook v.3.5"
    assert spells["arkal-acid-splash"]["imported_from_classes"] == wizard(0)
    # The reprint listed it at level 8; the kept Spell Compendium printing is level 5.
    assert spells["arkal-phantasmal-thief"]["source_book"] == "Spell Compendium"
    assert spells["arkal-phantasmal-thief"]["imported_from_classes"] == wizard(5)
    state = reopened.load_state()
    assert state["imported_classes"]["Wizard"]["levels"] == {
        "0": ["arkal-acid-splash", "arkal-light"],
        "5": ["arkal-phantasmal-thief"],
        "8": [],
    }
    assert state["index_metadata"]["spells"]["record_count"] == 3
    book = state["spellbooks"]["grimoire"]
    assert book["batches"][0]["entities"][0]["id"] == "arkal-acid-splash"
    assert book["open_batch"]["entities"] == [{"kind": "spell", "id": "arkal-acid-splash"}]


def test_deleting_an_unknown_class_or_spellbook_fails(tmp_path):
    store = RuntimeStore(tmp_path / "runtime")

    with pytest.raises(StoreError, match="Unknown imported class: Wizard"):
        store.delete_imported_class("Wizard")
    with pytest.raises(StoreError, match="Unknown spellbook: missing"):
        store.delete_spellbook("missing")


def test_deleting_a_spellbook_removes_its_files_and_frees_its_id(tmp_path):
    store = RuntimeStore(tmp_path / "runtime")
    book = store.create_spellbook("Grimoire")
    segment = store.spellbook_dir(book["id"]) / "batches" / "batch-0001.pdf"
    segment.parent.mkdir()
    segment.write_bytes(b"%PDF")

    deleted = store.delete_spellbook(book["id"])

    assert deleted["name"] == "Grimoire"
    assert store.load_state()["spellbooks"] == {}
    assert list(store.spellbooks_dir.iterdir()) == []
    assert store.create_spellbook("Grimoire")["id"] == book["id"]


def test_failed_spellbook_delete_keeps_the_book_and_its_files(monkeypatch, tmp_path):
    store = RuntimeStore(tmp_path / "runtime")
    book = store.create_spellbook("Grimoire")
    full_pdf = store.spellbook_dir(book["id"]) / "full.pdf"
    full_pdf.write_bytes(b"%PDF")

    def fail_state_write(_state):
        raise OSError("disk full")

    monkeypatch.setattr(store, "save_state", fail_state_write)

    with pytest.raises(OSError, match="disk full"):
        store.delete_spellbook(book["id"])

    assert book["id"] in store.load_state()["spellbooks"]
    assert full_pdf.read_bytes() == b"%PDF"
    assert [path.name for path in store.spellbooks_dir.iterdir()] == [book["id"]]


def test_startup_finishes_interrupted_spellbook_deletes(tmp_path):
    runtime = tmp_path / "runtime"
    store = RuntimeStore(runtime)
    kept = store.create_spellbook("Kept")
    gone = store.create_spellbook("Gone")
    state = store.load_state()
    del state["spellbooks"][gone["id"]]
    store.save_state(state)
    # One process died before its delete reached state, the other after.
    for book_id, trash_name in ((kept["id"], ".deleted-uncommitted"), (gone["id"], ".deleted-committed")):
        trash = store.spellbooks_dir / trash_name
        trash.mkdir()
        store.spellbook_dir(book_id).rename(trash / book_id)

    reopened = RuntimeStore(runtime)

    assert [path.name for path in reopened.spellbooks_dir.iterdir()] == [kept["id"]]

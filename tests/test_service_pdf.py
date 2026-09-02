from pathlib import Path

from pypdf import PdfReader

from spellbook_builder.service import commit_open_batch, prefix_search, verify_append_only
from spellbook_builder.store import RuntimeStore
from spellbook_builder.util import sha256_file


def spell(spell_id: str, name: str, body: str = "Useful spell description.") -> dict:
    return {
        "schema_version": 1, "id": spell_id, "name": name, "normalized_name": name.casefold(),
        "source_url": "https://example.test/spell", "source_book": "Fixture", "source_page": 1,
        "school": "Conjuration", "subschool": None, "descriptors": [], "levels": [{"class": "Wizard", "level": 1, "notes": None}],
        "components": ["V", "S"], "casting_time": "1 action", "range": "Close", "target": None, "area": None,
        "effect": None, "duration": "1 round", "saving_throw": "None", "spell_resistance": "No",
        "body_sections": [{"heading": None, "paragraphs": [body]}], "tables": [],
        "content_blocks": [{"type": "paragraph", "text": body}], "raw_labels": {}, "imported_from_classes": [], "fetched_at": "2026-01-01T00:00:00+00:00",
    }


def test_prefix_search_is_beginning_only_and_deterministic():
    spells = {item["id"]: item for item in [spell("2", "Fire Shield"), spell("1", "Fireball"), spell("3", "Ball Lightning")]}
    assert [item["name"] for item in prefix_search(spells, "fire")] == ["Fire Shield", "Fireball"]
    assert prefix_search(spells, "ball") == [{"id": "3", "name": "Ball Lightning"}]


def test_two_batch_append_only_toc_numbering_and_diff(tmp_path: Path):
    store = RuntimeStore(tmp_path / "runtime")
    spells = {"magic-missile": spell("magic-missile", "Magic Missile"), "summon-monster-i": spell("summon-monster-i", "Summon Monster I")}
    store.save_spells(spells)
    monster = {
        "id": "dire-rat::fiendish-dire-rat", "name": "Fiendish Dire Rat", "fields": {"Hit Dice": "1d8+1", "Armor Class": "15"},
        "shared_sections": [{"type": "heading", "text": "Combat"}, {"type": "paragraph", "text": "Bites and may smite good."}],
        "source_url": "https://www.d20srd.org/srd/monsters/direRat.htm",
    }
    lists = {"summon_monster:1": {"entries": [{"display_name": "Fiendish dire rat", "monster_ref": monster["id"], "notes": [], "alignment": "LE"}]}}
    store.save_summon_indexes(lists, {monster["id"]: monster}, {"schema_version": 1, "unresolved": []})
    book = store.create_spellbook("Fixture Book")

    store.begin_batch(book["id"])
    store.add_open_entity(book["id"], {"kind": "spell", "id": "magic-missile"})
    after_one = commit_open_batch(store, book["id"])
    first = after_one["batches"][0]
    first_path = store.spellbook_dir(book["id"]) / first["segment"]
    first_hash = sha256_file(first_path)
    assert first["page_start"] == 1

    store.begin_batch(book["id"])
    store.add_open_entity(book["id"], {"kind": "spell", "id": "summon-monster-i"})
    after_two = commit_open_batch(store, book["id"])
    assert sha256_file(first_path) == first_hash
    second = after_two["batches"][1]
    assert second["page_start"] == first["page_end"] + 1
    assert [item["kind"] for item in second["entities"]] == ["spell", "summon_statblock"]
    assert second["entities"][1]["title"] == "Fiendish dire rat"

    book_dir = store.spellbook_dir(book["id"])
    append_path = book_dir / "exports" / second["id"] / "append.pdf"
    segment_path = book_dir / second["segment"]
    assert append_path.read_bytes() == segment_path.read_bytes()
    append_text = "\n".join(page.extract_text() or "" for page in PdfReader(str(append_path)).pages)
    assert str(second["page_start"]) in append_text
    assert "Table of Contents" not in append_text
    toc_text = "\n".join(page.extract_text() or "" for page in PdfReader(str(book_dir / "exports" / second["id"] / "toc.pdf")).pages)
    assert "Magic Missile" in toc_text and "Summon Monster I" in toc_text and "Fiendish dire rat" in toc_text
    full_text = "\n".join(page.extract_text() or "" for page in PdfReader(str(book_dir / "full.pdf")).pages)
    assert "Table of Contents" in full_text
    report = verify_append_only(store, book["id"])
    assert report["ok"], report["failures"]
    assert report["batches_checked"] == 2

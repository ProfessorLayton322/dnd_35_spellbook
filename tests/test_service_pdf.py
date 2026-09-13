from pathlib import Path

import pytest
from pypdf import PdfReader

from spellbook_builder.pdfgen import RENDERER_VERSION, render_batch_segment
from spellbook_builder.service import ServiceError, commit_open_batch, prefix_search, render_full_spellbook, verify_append_only
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


def fiendish_dire_rat() -> dict:
    return {
        "id": "dire-rat::fiendish-dire-rat", "name": "Fiendish Dire Rat", "fields": {"Hit Dice": "1d8+1", "Armor Class": "15"},
        "shared_sections": [{"type": "heading", "text": "Combat"}, {"type": "paragraph", "text": "Bites and may smite good."}],
        "source_url": "https://www.d20srd.org/srd/monsters/direRat.htm",
    }


def rule_count(pdf_path: Path) -> int:
    # Entity separator lines are the only stroked paths on content pages.
    return sum(
        1
        for page in PdfReader(str(pdf_path)).pages
        for _operands, operator in page.get_contents().operations
        if operator == b"S"
    )


def test_prefix_search_is_beginning_only_and_deterministic():
    spells = {item["id"]: item for item in [spell("2", "Fire Shield"), spell("1", "Fireball"), spell("3", "Ball Lightning")]}
    assert [item["name"] for item in prefix_search(spells, "fire")] == ["Fire Shield", "Fireball"]
    assert prefix_search(spells, "ball") == [{"id": "3", "name": "Ball Lightning"}]


def test_two_batch_append_only_toc_numbering_and_diff(tmp_path: Path):
    store = RuntimeStore(tmp_path / "runtime")
    spells = {"magic-missile": spell("magic-missile", "Magic Missile"), "summon-monster-i": spell("summon-monster-i", "Summon Monster I")}
    store.save_spells(spells)
    monster = fiendish_dire_rat()
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
    assert rule_count(first_path) == 0

    store.begin_batch(book["id"])
    store.add_open_entity(book["id"], {"kind": "spell", "id": "summon-monster-i"})
    after_two = commit_open_batch(store, book["id"])
    assert sha256_file(first_path) == first_hash
    second = after_two["batches"][1]
    assert second["page_start"] == first["page_end"] + 1
    assert [item["kind"] for item in second["entities"]] == ["spell", "summon_statblock"]
    assert second["entities"][1]["title"] == "Fiendish dire rat"
    # The new batch starts on a fresh page; its statblock follows the spell on that page.
    assert [item["page"] for item in second["entities"]] == [second["page_start"], second["page_start"]]
    assert second["page_count"] == 1
    assert rule_count(store.spellbook_dir(book["id"]) / second["segment"]) == 1

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

    # JSON paths are portable when written and legacy Windows separators remain
    # readable after moving the whole runtime directory to Linux or macOS.
    portable = store.get_spellbook(book["id"])
    assert portable["batches"][0]["segment"] == "batches/batch-0001.pdf"
    assert portable["exports"][-1]["append_pdf"] == "exports/batch-0002/append.pdf"
    for batch in portable["batches"]:
        batch["segment"] = batch["segment"].replace("/", "\\")
    for export in portable["exports"]:
        for key in ("toc_pdf", "append_pdf", "full_pdf"):
            export[key] = export[key].replace("/", "\\")
    store.update_spellbook(portable)
    moved_full = render_full_spellbook(store, book["id"])
    assert len(PdfReader(str(moved_full)).pages) >= 3
    assert verify_append_only(store, book["id"])["ok"]


def test_entities_flow_continuously_with_lines_between_them(tmp_path: Path):
    monster = fiendish_dire_rat()
    monsters = {monster["id"]: monster}
    spells = {
        "long": spell("long", "Long Spell", " ".join(["This description keeps going for several pages."] * 450)),
        "short-a": spell("short-a", "Short Spell A"),
        "short-b": spell("short-b", "Short Spell B"),
    }
    short = [
        {"kind": "spell", "id": "short-a", "title": "Short Spell A"},
        {"kind": "summon_statblock", "id": monster["id"], "title": "Fiendish dire rat"},
        {"kind": "spell", "id": "short-b", "title": "Short Spell B"},
    ]

    # Short spells and statblocks share a page instead of taking one page each.
    page_count, anchors = render_batch_segment(tmp_path / "short.pdf", short, spells, monsters, 5)
    assert page_count == 1
    assert [anchor["page"] for anchor in anchors] == [5, 5, 5]
    assert rule_count(tmp_path / "short.pdf") == 2

    # The entity after a multi-page spell starts on the page where that spell ends.
    long_only = [{"kind": "spell", "id": "long", "title": "Long Spell"}]
    long_pages, _ = render_batch_segment(tmp_path / "long.pdf", long_only, spells, monsters, 1)
    assert long_pages > 1
    _page_count, anchors = render_batch_segment(tmp_path / "mixed.pdf", long_only + short[:1], spells, monsters, 1)
    assert [anchor["page"] for anchor in anchors] == [1, long_pages]
    assert rule_count(tmp_path / "mixed.pdf") == 1


def test_older_renderer_spellbook_commits_next_batch_with_current_renderer(tmp_path: Path):
    store = RuntimeStore(tmp_path / "runtime")
    store.save_spells({"magic-missile": spell("magic-missile", "Magic Missile")})
    book = store.create_spellbook("Old Book")
    assert book["renderer_version"] == RENDERER_VERSION

    store.update_spellbook({**store.get_spellbook(book["id"]), "renderer_version": 1})
    store.begin_batch(book["id"])
    store.add_open_entity(book["id"], {"kind": "spell", "id": "magic-missile"})
    committed = commit_open_batch(store, book["id"])
    assert committed["renderer_version"] == RENDERER_VERSION
    assert committed["batches"][0]["renderer_version"] == RENDERER_VERSION

    store.update_spellbook({**store.get_spellbook(book["id"]), "renderer_version": RENDERER_VERSION + 1})
    store.begin_batch(book["id"])
    store.add_open_entity(book["id"], {"kind": "spell", "id": "magic-missile"})
    with pytest.raises(ServiceError, match="newer"):
        commit_open_batch(store, book["id"])

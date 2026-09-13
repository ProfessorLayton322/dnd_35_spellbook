from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

from pypdf import PdfReader

from .pdfgen import RENDERER_VERSION, concatenate_pdfs, pdf_content_hashes, render_batch_segment, render_toc
from .store import RuntimeStore
from .util import atomic_json_write, normalized_name, portable_relative_path, resolve_portable_path, sha256_file, utc_now


class ServiceError(RuntimeError):
    pass


def prefix_search(spells: dict[str, dict], prefix: str, limit: int = 25) -> list[dict]:
    wanted = normalized_name(prefix)
    if not wanted:
        return []
    matches = [spell for spell in spells.values() if spell.get("normalized_name", normalized_name(spell["name"])).startswith(wanted)]
    matches.sort(key=lambda spell: (spell.get("normalized_name", normalized_name(spell["name"])), spell["id"]))
    return [{"id": spell["id"], "name": spell["name"]} for spell in matches[:limit]]


def summon_key(spell_name: str) -> str | None:
    value = normalized_name(spell_name)
    match = re.fullmatch(r"summon monster (i{1,3}|iv|v|vi{0,3}|ix)", value)
    family = "summon_monster"
    if not match:
        match = re.fullmatch(r"summon nature(?:s| s) ally (i{1,3}|iv|v|vi{0,3}|ix)", value)
        family = "summon_natures_ally"
    if not match:
        return None
    roman = match.group(1).upper()
    levels = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9}
    return f"{family}:{levels[roman]}"


def expand_entities(open_entities: list[dict], spells: dict, summon_lists: dict) -> list[dict]:
    expanded: list[dict] = []
    for source in open_entities:
        if source["kind"] != "spell" or source["id"] not in spells:
            raise ServiceError(f"Unknown spell entity: {source}")
        spell = spells[source["id"]]
        expanded.append({"kind": "spell", "id": spell["id"], "title": spell["name"]})
        key = summon_key(spell["name"])
        if key:
            summon_list = summon_lists.get(key)
            if not summon_list:
                raise ServiceError(f"{spell['name']} needs its summon creatures; choose Import summons in the Data section (CLI: build-summon-index)")
            for entry in summon_list["entries"]:
                refs = entry.get("monster_refs") or ([entry["monster_ref"]] if entry.get("monster_ref") else [])
                if not refs:
                    raise ServiceError(f"Unresolved summon entry for {spell['name']}: {entry['display_name']}")
                names = entry.get("resolved_names", [])
                for index, ref in enumerate(refs):
                    title = entry["display_name"]
                    if len(refs) > 1:
                        title += " — " + (names[index] if index < len(names) else ref.rsplit("::", 1)[-1].replace("-", " ").title())
                    expanded.append(
                        {
                            "kind": "summon_statblock",
                            "id": ref,
                            "title": title,
                            "summon_list": key,
                            "notes": entry.get("notes", []),
                            "alignment": entry.get("alignment"),
                        }
                    )
    return expanded


def _copy_atomic(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(fd)
    temp = Path(name)
    try:
        shutil.copyfile(source, temp)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def all_toc_entries(book: dict) -> list[dict]:
    return [
        {"title": entity["title"], "page": entity["page"], "kind": entity["kind"], "id": entity["id"]}
        for batch in book["batches"]
        for entity in batch["entities"]
    ]


def commit_open_batch(store: RuntimeStore, spellbook_id: str) -> dict:
    book = store.get_spellbook(spellbook_id)
    open_batch = book.get("open_batch")
    if not open_batch:
        raise ServiceError("There is no open batch")
    if not open_batch["entities"]:
        raise ServiceError("Cannot commit an empty batch")
    # A newer renderer only affects the batch being committed; historical
    # segments keep their original layout and are never re-rendered.
    book_renderer = book.get("renderer_version", 1)
    if book_renderer > RENDERER_VERSION:
        raise ServiceError(f"Spellbook uses renderer {book_renderer}, newer than this application's renderer {RENDERER_VERSION}")
    spells = store.load_spells()
    monsters = store.load_monsters()
    expanded = expand_entities(open_batch["entities"], spells, store.load_summon_lists())
    for entity in expanded:
        if entity["kind"] == "summon_statblock" and entity["id"] not in monsters:
            raise ServiceError(f"Summon statblock is absent from monster index: {entity['id']}")
    batch_id = open_batch["id"]
    book_dir = store.spellbook_dir(spellbook_id)
    segment = book_dir / "batches" / f"{batch_id}.pdf"
    if segment.exists():
        raise ServiceError(f"Refusing to overwrite immutable batch segment: {segment}")
    page_start = book["content_page_count"] + 1
    page_count, anchors = render_batch_segment(segment, expanded, spells, monsters, page_start)
    anchor_map = {entry["entity_index"]: entry for entry in anchors}
    for index, entity in enumerate(expanded):
        entity["page"] = anchor_map[index]["page"]
    committed_at = utc_now()
    batch = {
        "id": batch_id,
        "created_at": open_batch["created_at"],
        "committed_at": committed_at,
        "entities": expanded,
        "page_start": page_start,
        "page_end": page_start + page_count - 1,
        "page_count": page_count,
        "segment": portable_relative_path(segment, book_dir),
        "segment_sha256": sha256_file(segment),
        "renderer_version": RENDERER_VERSION,
    }
    candidate = dict(book)
    candidate["batches"] = book["batches"] + [batch]
    candidate["open_batch"] = None
    candidate["renderer_version"] = RENDERER_VERSION
    candidate["content_page_count"] = batch["page_end"]
    export_dir = book_dir / "exports" / batch_id
    toc_path = export_dir / "toc.pdf"
    append_path = export_dir / "append.pdf"
    render_toc(toc_path, candidate["name"], all_toc_entries(candidate))
    _copy_atomic(segment, append_path)
    full_path = book_dir / "full.pdf"
    segments = [resolve_portable_path(book_dir, historical["segment"]) for historical in candidate["batches"]]
    concatenate_pdfs(full_path, [toc_path, *segments])
    manifest = {
        "schema_version": 1,
        "renderer_version": RENDERER_VERSION,
        "spellbook_id": spellbook_id,
        "batch_id": batch_id,
        "committed_at": committed_at,
        "toc_pdf": portable_relative_path(toc_path, book_dir),
        "append_pdf": portable_relative_path(append_path, book_dir),
        "full_pdf": portable_relative_path(full_path, book_dir),
        "logical_content_page_start": batch["page_start"],
        "logical_content_page_end": batch["page_end"],
        "new_page_count": page_count,
        "cumulative_content_page_count": candidate["content_page_count"],
        "entities": [{"kind": item["kind"], "id": item["id"], "title": item["title"], "page": item["page"]} for item in expanded],
        "segment_sha256": batch["segment_sha256"],
        "append_sha256": sha256_file(append_path),
        "toc_sha256": sha256_file(toc_path),
        "full_sha256": sha256_file(full_path),
    }
    atomic_json_write(export_dir / "manifest.json", manifest)
    candidate["exports"] = book["exports"] + [manifest]
    store.update_spellbook(candidate)
    return candidate


def render_full_spellbook(store: RuntimeStore, spellbook_id: str) -> Path:
    book = store.get_spellbook(spellbook_id)
    book_dir = store.spellbook_dir(spellbook_id)
    toc = book_dir / "current-toc.pdf"
    render_toc(toc, book["name"], all_toc_entries(book))
    segments = [resolve_portable_path(book_dir, batch["segment"]) for batch in book["batches"]]
    full = book_dir / "full.pdf"
    concatenate_pdfs(full, [toc, *segments])
    return full


def verify_append_only(store: RuntimeStore, spellbook_id: str) -> dict:
    book = store.get_spellbook(spellbook_id)
    if not book["batches"]:
        raise ServiceError("Spellbook has no committed batches")
    book_dir = store.spellbook_dir(spellbook_id)
    full = book_dir / "full.pdf"
    if not full.exists():
        raise ServiceError("Full PDF is missing")
    total_content = sum(batch["page_count"] for batch in book["batches"])
    toc_pages = len(PdfReader(str(full)).pages) - total_content
    full_hashes = pdf_content_hashes(full)
    checked = 0
    failures: list[str] = []
    offset = toc_pages
    for batch in book["batches"]:
        segment = resolve_portable_path(book_dir, batch["segment"])
        segment_hashes = pdf_content_hashes(segment)
        if sha256_file(segment) != batch["segment_sha256"]:
            failures.append(f"{batch['id']}: immutable segment file hash changed")
        if full_hashes[offset : offset + len(segment_hashes)] != segment_hashes:
            failures.append(f"{batch['id']}: full PDF content streams differ from segment")
        append_path = book_dir / "exports" / batch["id"] / "append.pdf"
        if pdf_content_hashes(append_path) != segment_hashes:
            failures.append(f"{batch['id']}: append PDF differs from segment")
        offset += len(segment_hashes)
        checked += len(segment_hashes)
    expected_start = 1
    for batch in book["batches"]:
        if batch["page_start"] != expected_start:
            failures.append(f"{batch['id']}: starts at {batch['page_start']}, expected {expected_start}")
        expected_start = batch["page_end"] + 1
    return {"ok": not failures, "spellbook_id": spellbook_id, "toc_pages": toc_pages, "content_pages_checked": checked, "batches_checked": len(book["batches"]), "failures": failures}


def validate_indexes(store: RuntimeStore) -> dict:
    spells = store.load_spells()
    summon_lists = store.load_summon_lists()
    monsters = store.load_monsters()
    errors: list[str] = []
    for spell in spells.values():
        if not spell.get("name"):
            errors.append(f"spell {spell.get('id')} has no name")
        if not spell.get("content_blocks"):
            errors.append(f"spell {spell.get('id')} has no description")
    for key, summon_list in summon_lists.items():
        if not summon_list.get("entries"):
            errors.append(f"summon list {key} is empty")
        for entry in summon_list.get("entries", []):
            refs = entry.get("monster_refs") or ([entry["monster_ref"]] if entry.get("monster_ref") else [])
            if not refs:
                errors.append(f"{key}: unresolved {entry['display_name']}")
            for ref in refs:
                if ref not in monsters:
                    errors.append(f"{key}: missing statblock {ref}")
    if summon_lists and len(summon_lists) != 18:
        errors.append(f"expected 18 summon lists, found {len(summon_lists)}")
    return {"ok": not errors, "spell_count": len(spells), "summon_list_count": len(summon_lists), "monster_statblock_count": len(monsters), "errors": errors}

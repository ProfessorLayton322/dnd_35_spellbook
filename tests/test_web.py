import asyncio
import json
from urllib.parse import quote, unquote

import pytest
from fastapi.responses import JSONResponse
from starlette.requests import Request

from spellbook_builder import web
from spellbook_builder.service import commit_open_batch
from test_service_pdf import spell


def route_endpoint(app, path: str):
    return next(route.endpoint for route in app.routes if getattr(route, "path", None) == path)


def read_ndjson(response) -> list[dict]:
    async def read_stream():
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
        return "".join(chunks)

    return [json.loads(line) for line in asyncio.run(read_stream()).splitlines()]


def test_streaming_class_import_reports_done_and_persists(monkeypatch, tmp_path):
    def fake_import(url, fetcher, on_progress=None, on_event=None):
        on_event(
            {
                "type": "class_discovered",
                "class_name": "Fixture Mage",
                "completed_levels": 0,
                "total_levels": 1,
                "downloaded_spells": 0,
                "total_spells": None,
            }
        )
        on_event(
            {
                "type": "spell_downloaded",
                "class_name": "Fixture Mage",
                "level": 0,
                "spell_name": "Flame Orb",
                "completed_levels": 0,
                "total_levels": 1,
                "downloaded_spells": 1,
                "total_spells": 1,
            }
        )
        on_event(
            {
                "type": "level_completed",
                "class_name": "Fixture Mage",
                "level": 0,
                "spells_in_level": 1,
                "completed_levels": 1,
                "total_levels": 1,
                "downloaded_spells": 1,
                "total_spells": 1,
            }
        )
        class_record = {
            "schema_version": 1,
            "class_name": "Fixture Mage",
            "source_url": url,
            "levels": {"0": ["flame-orb"]},
            "imported_at": "2026-09-11T00:00:00+00:00",
        }
        return class_record, {"flame-orb": {"id": "flame-orb", "imported_from_classes": []}}

    monkeypatch.setattr(web, "import_class", fake_import)
    app = web.create_app(tmp_path / "runtime")
    endpoint = route_endpoint(app, "/api/import-class")

    events = read_ndjson(endpoint(url="https://dnd.arkalseif.info/classes/fixture-mage/index.html"))
    assert [event["type"] for event in events] == [
        "class_discovered",
        "spell_downloaded",
        "level_completed",
        "done",
    ]
    assert events[-1]["downloaded_spells"] == events[-1]["total_spells"] == 1
    assert app.state.store.load_state()["imported_classes"]["Fixture Mage"]["levels"] == {"0": ["flame-orb"]}


def test_streaming_class_import_rejects_other_hosts(tmp_path):
    app = web.create_app(tmp_path / "runtime")
    endpoint = route_endpoint(app, "/api/import-class")

    response = endpoint(url="https://example.com/classes/wizard")

    assert isinstance(response, JSONResponse)
    assert response.status_code == 400
    assert json.loads(response.body)["error"] == "Enter a dnd.arkalseif.info class URL"


def test_delete_routes_remove_spellbooks_and_class_spell_lists(tmp_path):
    app = web.create_app(tmp_path / "runtime")
    store = app.state.store
    store.merge_class_import(
        {"class_name": "Fixture Mage", "levels": {"0": ["flame-orb"]}},
        {"flame-orb": {"id": "flame-orb", "imported_from_classes": [{"class_name": "Fixture Mage", "spell_level": 0}]}},
    )
    book = store.create_spellbook("Grimoire")
    index = route_endpoint(app, "/")(Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}))
    page = index.body.decode()
    assert 'action="/spellbooks/grimoire/delete"' in page
    assert 'action="/imported-classes/delete"' in page and 'name="class_name" value="Fixture Mage"' in page

    delete_book = route_endpoint(app, "/spellbooks/{book_id}/delete")
    response = delete_book(book_id=book["id"])

    assert response.status_code == 303
    assert response.headers["location"] == "/?message=" + quote("Deleted spellbook Grimoire")
    assert store.load_state()["spellbooks"] == {}
    assert delete_book(book_id=book["id"]).headers["location"].endswith("&error=" + quote("Unknown spellbook: grimoire"))

    response = route_endpoint(app, "/imported-classes/delete")(class_name="Fixture Mage")

    assert response.headers["location"] == "/?message=" + quote("Deleted Fixture Mage spell list; removed 1 unused spell(s)")
    assert store.load_state()["imported_classes"] == {}
    assert store.load_spells() == {}


def test_export_files_open_inline_with_descriptive_names(tmp_path):
    app = web.create_app(tmp_path / "runtime")
    store = app.state.store
    store.save_spells({"magic-missile": spell("magic-missile", "Magic Missile")})
    book = store.create_spellbook("Fire/Ice Grimoire")
    store.begin_batch(book["id"])
    store.add_open_entity(book["id"], {"kind": "spell", "id": "magic-missile"})
    commit_open_batch(store, book["id"])
    files = route_endpoint(app, "/spellbooks/{book_id}/files/{kind}")

    def disposition(kind: str) -> str:
        return files(book_id=book["id"], kind=kind).headers["content-disposition"]

    assert disposition("full") == "inline; filename*=utf-8''" + quote("Fire-Ice Grimoire - full spellbook.pdf")
    assert disposition("toc") == "inline; filename*=utf-8''" + quote("Fire-Ice Grimoire - batch-0001 table of contents.pdf")
    assert disposition("append") == "inline; filename*=utf-8''" + quote("Fire-Ice Grimoire - batch-0001 new pages.pdf")
    assert disposition("manifest") == "inline; filename*=utf-8''" + quote("Fire-Ice Grimoire - batch-0001 manifest.json")


def test_export_files_are_served_from_a_runtime_path_through_a_symlink(tmp_path):
    # Android's files directory is /data/user/0/<package>/files, and /data/user/0 links to /data/data.
    (tmp_path / "data").mkdir()
    try:
        (tmp_path / "user").symlink_to(tmp_path / "data", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available")
    app = web.create_app(tmp_path / "user" / "runtime")
    store = app.state.store
    store.save_spells({"magic-missile": spell("magic-missile", "Magic Missile")})
    book = store.create_spellbook("Grimoire")
    store.begin_batch(book["id"])
    store.add_open_entity(book["id"], {"kind": "spell", "id": "magic-missile"})
    commit_open_batch(store, book["id"])
    files = route_endpoint(app, "/spellbooks/{book_id}/files/{kind}")

    assert {kind: files(book_id=book["id"], kind=kind).status_code for kind in ("full", "toc", "append", "manifest")} == {
        "full": 200,
        "toc": 200,
        "append": 200,
        "manifest": 200,
    }


def fake_summon_build(unresolved: list[dict]):
    def build(fetcher, on_progress=None, on_event=None):
        emit = on_event or (lambda _event: None)
        emit({"type": "summon_table_parsed", "spell_name": "Summon Monster I", "entries": 1, "parsed_tables": 1, "total_tables": 1, "downloaded_monster_pages": 0, "total_monster_pages": None})
        emit({"type": "monster_download_started", "parsed_tables": 1, "total_tables": 1, "downloaded_monster_pages": 0, "total_monster_pages": 1})
        emit({"type": "monster_page_downloaded", "monster_name": "Dire Rat", "variants": 2, "parsed_tables": 1, "total_tables": 1, "downloaded_monster_pages": 1, "total_monster_pages": 1})
        ref = "dire-rat::fiendish-dire-rat"
        lists = {"summon_monster:1": {"spell_name": "Summon Monster I", "entries": [{"display_name": "Fiendish dire rat", "monster_refs": [ref]}]}}
        metadata = {
            "schema_version": 1,
            "built_at": "2026-09-13T08:00:00+00:00",
            "summon_page_count": 1,
            "monster_page_count": 1,
            "statblock_count": 1,
            "entry_count": 1,
            "unresolved": unresolved,
        }
        return lists, {ref: {"id": ref, "name": "Fiendish Dire Rat"}}, metadata

    return build


def render_index(app) -> str:
    return route_endpoint(app, "/")(Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""})).body.decode()


def test_streaming_summon_import_reports_done_and_persists(monkeypatch, tmp_path):
    monkeypatch.setattr(web, "build_summon_index", fake_summon_build([]))
    app = web.create_app(tmp_path / "runtime")
    before = render_index(app)
    assert 'action="/build-summon-index"' in before and "Import summons" in before and "Not imported yet" in before

    events = read_ndjson(route_endpoint(app, "/api/build-summon-index")())

    assert [event["type"] for event in events] == ["summon_table_parsed", "monster_download_started", "monster_page_downloaded", "done"]
    message = "Imported summons: 1 creature statblocks from 1 summon tables"
    assert events[-1]["message"] == message
    assert events[-1]["redirect"] == "/?message=" + quote(message)
    assert events[-1]["downloaded_monster_pages"] == events[-1]["total_monster_pages"] == 1
    assert list(app.state.store.load_summon_lists()) == ["summon_monster:1"]
    assert app.state.store.load_state()["index_metadata"]["summons"]["statblock_count"] == 1
    after = render_index(app)
    assert "Re-import summons" in after and "1 creature statblocks from 1 Summon Monster" in after and "imported 2026-09-13" in after


def test_summon_import_reports_unresolved_entries_as_error(monkeypatch, tmp_path):
    unresolved = [{"list": "summon_monster:1", "display_name": "Fiendish dire rat", "target_url": None, "reason": "no monster link"}]
    monkeypatch.setattr(web, "build_summon_index", fake_summon_build(unresolved))
    app = web.create_app(tmp_path / "runtime")

    response = route_endpoint(app, "/build-summon-index")()

    assert response.status_code == 303
    assert unquote(response.headers["location"]) == (
        "/?error=Imported summons: 1 creature statblocks from 1 summon tables; "
        "1 summon entries have no matching statblock: Fiendish dire rat (Summon Monster I)"
    )
    assert "1 entries unresolved" in render_index(app)


def test_streaming_summon_import_reports_fetch_failure(monkeypatch, tmp_path):
    def failing_build(fetcher, on_progress=None, on_event=None):
        raise RuntimeError("Could not fetch https://www.d20srd.org/srd/spells/summonMonsterI.htm")

    monkeypatch.setattr(web, "build_summon_index", failing_build)
    app = web.create_app(tmp_path / "runtime")

    events = read_ndjson(route_endpoint(app, "/api/build-summon-index")())

    assert events == [{"type": "error", "message": "Could not fetch https://www.d20srd.org/srd/spells/summonMonsterI.htm"}]
    assert app.state.store.load_summon_lists() == {}

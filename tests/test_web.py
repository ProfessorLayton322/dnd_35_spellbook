import asyncio
import json

from fastapi.responses import JSONResponse

from spellbook_builder import web


def route_endpoint(app, path: str):
    return next(route.endpoint for route in app.routes if getattr(route, "path", None) == path)


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

    response = endpoint(url="https://dnd.arkalseif.info/classes/fixture-mage/index.html")

    async def read_stream():
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
        return "".join(chunks)

    events = [json.loads(line) for line in asyncio.run(read_stream()).splitlines()]
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

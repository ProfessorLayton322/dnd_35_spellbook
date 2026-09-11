from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from queue import Queue
from threading import Thread
from urllib.parse import quote
from urllib.parse import urlparse

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .arkal import import_class
from .fetch import Fetcher
from .service import commit_open_batch, prefix_search
from .store import RuntimeStore
from .util import resolve_portable_path


PACKAGE_ROOT = Path(__file__).resolve().parent


def _validate_class_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname != "dnd.arkalseif.info":
        raise ValueError("Enter a dnd.arkalseif.info class URL")


def create_app(runtime_dir: Path) -> FastAPI:
    app = FastAPI(title="D&D 3.5 Spellbook Builder")
    app.state.store = RuntimeStore(runtime_dir)
    templates = Jinja2Templates(directory=str(PACKAGE_ROOT / "templates"))
    app.mount("/static", StaticFiles(directory=str(PACKAGE_ROOT / "static")), name="static")

    def redirect(book_id: str | None = None, message: str | None = None, error: str | None = None):
        params = []
        if book_id:
            params.append("book=" + quote(book_id))
        if message:
            params.append("message=" + quote(message))
        if error:
            params.append("error=" + quote(error))
        return RedirectResponse("/?" + "&".join(params), status_code=303)

    @app.get("/")
    def index(request: Request, book: str | None = None, message: str | None = None, error: str | None = None):
        store: RuntimeStore = app.state.store
        state = store.load_state()
        selected = state["spellbooks"].get(book) if book else next(iter(state["spellbooks"].values()), None)
        spells = store.load_spells()
        open_items = []
        if selected and selected.get("open_batch"):
            for item in selected["open_batch"]["entities"]:
                open_items.append({**item, "title": spells.get(item["id"], {}).get("name", item["id"])})
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "state": state,
                "selected": selected,
                "open_items": open_items,
                "message": message,
                "error": error,
            },
        )

    @app.post("/import-class")
    def import_class_route(url: str = Form(...)):
        try:
            _validate_class_url(url)
            latest_progress: dict = {}
            class_record, spells = import_class(url, Fetcher(), on_event=latest_progress.update)
            app.state.store.merge_class_import(class_record, spells)
            counts = ", ".join(f"L{level}: {len(ids)}" for level, ids in class_record["levels"].items())
            skipped = latest_progress.get("skipped_spells", 0)
            skipped_note = f"; skipped {skipped} missing spell pages" if skipped else ""
            return redirect(message=f"Imported {class_record['class_name']} ({counts}){skipped_note}")
        except Exception as exc:
            return redirect(error=str(exc))

    @app.post("/api/import-class")
    def import_class_stream_route(url: str = Form(...)):
        try:
            _validate_class_url(url)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        events: Queue[dict | None] = Queue()

        def run_import() -> None:
            latest_progress: dict = {}

            def publish(event: dict) -> None:
                latest_progress.update(event)
                events.put(event)

            try:
                class_record, spells = import_class(url, Fetcher(), on_event=publish)
                app.state.store.merge_class_import(class_record, spells)
                counts = ", ".join(f"L{level}: {len(ids)}" for level, ids in class_record["levels"].items())
                downloaded_spells = latest_progress.get("downloaded_spells", 0)
                skipped_spells = latest_progress.get("skipped_spells", 0)
                processed_spells = latest_progress.get("processed_spells", downloaded_spells + skipped_spells)
                total_spells = latest_progress.get("total_spells", downloaded_spells)
                skipped_note = f"; skipped {skipped_spells} missing spell pages" if skipped_spells else ""
                message = f"Imported {class_record['class_name']} ({counts}){skipped_note}"
                events.put(
                    {
                        "type": "done",
                        "class_name": class_record["class_name"],
                        "completed_levels": len(class_record["levels"]),
                        "total_levels": len(class_record["levels"]),
                        "downloaded_spells": downloaded_spells,
                        "skipped_spells": skipped_spells,
                        "processed_spells": processed_spells,
                        "total_spells": total_spells,
                        "unique_spells": len(spells),
                        "message": message,
                        "redirect": "/?message=" + quote(message),
                    }
                )
            except Exception as exc:
                events.put({"type": "error", "message": str(exc)})
            finally:
                events.put(None)

        Thread(target=run_import, name="class-import", daemon=True).start()

        def stream_events() -> Iterator[str]:
            while True:
                event = events.get()
                if event is None:
                    break
                yield json.dumps(event, ensure_ascii=False) + "\n"

        return StreamingResponse(
            stream_events(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/spellbooks")
    def create_spellbook_route(name: str = Form(...)):
        try:
            created = app.state.store.create_spellbook(name)
            return redirect(created["id"], "Spellbook created")
        except Exception as exc:
            return redirect(error=str(exc))

    @app.post("/spellbooks/{book_id}/begin")
    def begin_route(book_id: str):
        try:
            app.state.store.begin_batch(book_id)
            return redirect(book_id, "Batch started")
        except Exception as exc:
            return redirect(book_id, error=str(exc))

    @app.post("/spellbooks/{book_id}/add-spell")
    def add_spell_route(book_id: str, spell_id: str = Form(...)):
        try:
            spells = app.state.store.load_spells()
            if spell_id not in spells:
                raise ValueError("Select a spell from the prefix suggestions")
            app.state.store.add_open_entity(book_id, {"kind": "spell", "id": spell_id})
            return redirect(book_id, f"Added {spells[spell_id]['name']}")
        except Exception as exc:
            return redirect(book_id, error=str(exc))

    @app.post("/spellbooks/{book_id}/add-class-level")
    def add_class_level_route(book_id: str, class_name: str = Form(...), level: int = Form(...)):
        try:
            state = app.state.store.load_state()
            class_record = state["imported_classes"].get(class_name)
            if not class_record or str(level) not in class_record["levels"]:
                raise ValueError("Unknown imported class/level")
            before = len(app.state.store.get_spellbook(book_id)["open_batch"]["entities"])
            for spell_id in class_record["levels"][str(level)]:
                app.state.store.add_open_entity(book_id, {"kind": "spell", "id": spell_id})
            after = len(app.state.store.get_spellbook(book_id)["open_batch"]["entities"])
            return redirect(book_id, f"Added {after - before} spells from {class_name} level {level}")
        except Exception as exc:
            return redirect(book_id, error=str(exc))

    @app.post("/spellbooks/{book_id}/remove/{index}")
    def remove_route(book_id: str, index: int):
        try:
            app.state.store.remove_open_entity(book_id, index)
            return redirect(book_id, "Removed batch item")
        except Exception as exc:
            return redirect(book_id, error=str(exc))

    @app.post("/spellbooks/{book_id}/commit")
    def commit_route(book_id: str):
        try:
            book = commit_open_batch(app.state.store, book_id)
            batch = book["batches"][-1]
            return redirect(book_id, f"Committed {batch['id']} as content pages {batch['page_start']}–{batch['page_end']}")
        except Exception as exc:
            return redirect(book_id, error=str(exc))

    @app.get("/api/spells")
    def spell_search(prefix: str = Query("", max_length=100)):
        return JSONResponse(prefix_search(app.state.store.load_spells(), prefix))

    @app.get("/spellbooks/{book_id}/files/{kind}")
    def file_route(book_id: str, kind: str):
        book = app.state.store.get_spellbook(book_id)
        book_dir = app.state.store.spellbook_dir(book_id)
        if kind == "full":
            path = book_dir / "full.pdf"
        elif kind in {"toc", "append", "manifest"} and book["exports"]:
            last = book["exports"][-1]
            key = {"toc": "toc_pdf", "append": "append_pdf"}.get(kind)
            stored_path = last[key] if key else f"exports/{last['batch_id']}/manifest.json"
            path = resolve_portable_path(book_dir, stored_path)
        else:
            return JSONResponse({"error": "File not available"}, status_code=404)
        if not path.is_file() or book_dir not in path.resolve().parents:
            return JSONResponse({"error": "File not available"}, status_code=404)
        return FileResponse(path)

    return app

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
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
from .srd import build_summon_index
from .store import RuntimeStore
from .util import resolve_portable_path


PACKAGE_ROOT = Path(__file__).resolve().parent
EXPORT_LABELS = {"toc": "table of contents", "append": "new pages", "manifest": "manifest"}


def _export_filename(book_name: str, label: str, suffix: str) -> str:
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", book_name).strip(" .") or "spellbook"
    return f"{safe_name} - {label}{suffix}"


def _validate_class_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname != "dnd.arkalseif.info":
        raise ValueError("Enter a dnd.arkalseif.info class URL")


def _summon_import_message(lists: dict, metadata: dict) -> str:
    message = f"Imported summons: {metadata['statblock_count']} creature statblocks from {metadata['summon_page_count']} summon tables"
    unresolved = metadata["unresolved"]
    if not unresolved:
        return message
    names = ", ".join(f"{item['display_name']} ({lists[item['list']]['spell_name']})" for item in unresolved[:5])
    more = f" and {len(unresolved) - 5} more" if len(unresolved) > 5 else ""
    return f"{message}; {len(unresolved)} summon entries have no matching statblock: {names}{more}"


def _stream_progress(thread_name: str, job: Callable[[Callable[[dict], None]], dict]) -> StreamingResponse:
    """Run a slow import in a thread and stream its progress as NDJSON.

    ``job`` receives a callback for progress events and returns the fields of
    the final ``done`` event; an exception becomes an ``error`` event.
    """

    events: Queue[dict | None] = Queue()

    def run() -> None:
        try:
            events.put({"type": "done", **job(events.put)})
        except Exception as exc:
            events.put({"type": "error", "message": str(exc)})
        finally:
            events.put(None)

    Thread(target=run, name=thread_name, daemon=True).start()

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

        def run_import(publish: Callable[[dict], None]) -> dict:
            latest_progress: dict = {}

            def on_event(event: dict) -> None:
                latest_progress.update(event)
                publish(event)

            class_record, spells = import_class(url, Fetcher(), on_event=on_event)
            app.state.store.merge_class_import(class_record, spells)
            counts = ", ".join(f"L{level}: {len(ids)}" for level, ids in class_record["levels"].items())
            downloaded_spells = latest_progress.get("downloaded_spells", 0)
            skipped_spells = latest_progress.get("skipped_spells", 0)
            processed_spells = latest_progress.get("processed_spells", downloaded_spells + skipped_spells)
            total_spells = latest_progress.get("total_spells", downloaded_spells)
            skipped_note = f"; skipped {skipped_spells} missing spell pages" if skipped_spells else ""
            message = f"Imported {class_record['class_name']} ({counts}){skipped_note}"
            return {
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

        return _stream_progress("class-import", run_import)

    def import_summons(on_event: Callable[[dict], None] | None = None) -> tuple[str, dict]:
        lists, monsters, metadata = build_summon_index(Fetcher(), on_event=on_event)
        # Unresolved entries are saved too, matching the CLI, and reported as an error.
        app.state.store.save_summon_indexes(lists, monsters, metadata)
        return _summon_import_message(lists, metadata), metadata

    @app.post("/build-summon-index")
    def build_summon_index_route():
        try:
            message, metadata = import_summons()
            return redirect(error=message) if metadata["unresolved"] else redirect(message=message)
        except Exception as exc:
            return redirect(error=str(exc))

    @app.post("/api/build-summon-index")
    def build_summon_index_stream_route():
        def run_build(publish: Callable[[dict], None]) -> dict:
            message, metadata = import_summons(publish)
            flash = "error" if metadata["unresolved"] else "message"
            return {
                "parsed_tables": metadata["summon_page_count"],
                "total_tables": metadata["summon_page_count"],
                "downloaded_monster_pages": metadata["monster_page_count"],
                "total_monster_pages": metadata["monster_page_count"],
                "statblocks": metadata["statblock_count"],
                "unresolved": len(metadata["unresolved"]),
                "message": message,
                "redirect": f"/?{flash}=" + quote(message),
            }

        return _stream_progress("summon-import", run_build)

    @app.post("/spellbooks")
    def create_spellbook_route(name: str = Form(...)):
        try:
            created = app.state.store.create_spellbook(name)
            return redirect(created["id"], "Spellbook created")
        except Exception as exc:
            return redirect(error=str(exc))

    @app.post("/spellbooks/{book_id}/delete")
    def delete_spellbook_route(book_id: str):
        try:
            deleted = app.state.store.delete_spellbook(book_id)
            return redirect(message=f"Deleted spellbook {deleted['name']}")
        except Exception as exc:
            return redirect(book_id, error=str(exc))

    @app.post("/imported-classes/delete")
    def delete_imported_class_route(class_name: str = Form(...)):
        try:
            removed = app.state.store.delete_imported_class(class_name)
            return redirect(message=f"Deleted {class_name} spell list; removed {removed} unused spell(s)")
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
            label = "full spellbook"
        elif kind in {"toc", "append", "manifest"} and book["exports"]:
            last = book["exports"][-1]
            key = {"toc": "toc_pdf", "append": "append_pdf"}.get(kind)
            stored_path = last[key] if key else f"exports/{last['batch_id']}/manifest.json"
            path = resolve_portable_path(book_dir, stored_path)
            label = f"{last['batch_id']} {EXPORT_LABELS[kind]}"
        else:
            return JSONResponse({"error": "File not available"}, status_code=404)
        # Resolve both sides: Android's runtime path runs through the /data/user/0 -> /data/data symlink.
        if not path.is_file() or book_dir.resolve() not in path.resolve().parents:
            return JSONResponse({"error": "File not available"}, status_code=404)
        # Inline keeps browsers showing the file; the name is used when it is saved.
        return FileResponse(path, filename=_export_filename(book["name"], label, path.suffix), content_disposition_type="inline")

    return app

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .arkal import import_class
from .fetch import Fetcher
from .service import prefix_search, render_full_spellbook, validate_indexes, verify_append_only
from .srd import build_summon_index
from .store import RuntimeStore
from .util import default_runtime_dir


def _store(args) -> RuntimeStore:
    return RuntimeStore(Path(args.runtime).expanduser().resolve())


def _fetcher(args) -> Fetcher:
    return Fetcher(timeout=args.timeout, delay=args.delay)


def cmd_import_class(args) -> int:
    store = _store(args)
    latest_progress: dict = {}
    class_record, spells = import_class(args.url, _fetcher(args), print, latest_progress.update)
    store.merge_class_import(class_record, spells)
    print(f"Imported {class_record['class_name']}")
    for level, ids in sorted(class_record["levels"].items(), key=lambda pair: int(pair[0])):
        print(f"  level {level}: {len(ids)} spells")
    print(f"  unique records in import: {len(spells)}")
    if latest_progress.get("skipped_spells"):
        print(f"  missing spell pages skipped: {latest_progress['skipped_spells']}")
    return 0


def cmd_build_summon(args) -> int:
    store = _store(args)
    lists, monsters, metadata = build_summon_index(_fetcher(args), print)
    store.save_summon_indexes(lists, monsters, metadata)
    print(json.dumps(metadata, indent=2, ensure_ascii=False))
    return 1 if metadata["unresolved"] else 0


def cmd_validate(args) -> int:
    report = validate_indexes(_store(args))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1


def cmd_list_spells(args) -> int:
    spells = _store(args).load_spells()
    if args.prefix:
        values = prefix_search(spells, args.prefix, limit=args.limit)
    else:
        values = sorted(({"id": spell["id"], "name": spell["name"]} for spell in spells.values()), key=lambda item: (item["name"].casefold(), item["id"]))[: args.limit]
    for value in values:
        print(f"{value['name']}\t{value['id']}")
    return 0


def cmd_render(args) -> int:
    path = render_full_spellbook(_store(args), args.spellbook_id)
    print(path)
    return 0


def cmd_verify(args) -> int:
    report = verify_append_only(_store(args), args.spellbook_id)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1


def cmd_serve(args) -> int:
    import uvicorn

    from .web import create_app

    uvicorn.run(create_app(Path(args.runtime).expanduser().resolve()), host=args.host, port=args.port)
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="spellbook", description="Local D&D 3.5 spellbook builder")
    result.add_argument("--runtime", default=str(default_runtime_dir()), help="runtime/user-data directory (or DND_SPELLBOOK_RUNTIME)")
    result.add_argument("--timeout", type=float, default=25.0, help="HTTP timeout in seconds")
    result.add_argument("--delay", type=float, default=0.15, help="minimum delay between remote requests")
    commands = result.add_subparsers(dest="command", required=True)

    import_command = commands.add_parser("import-class", help="import an Arkalself class and its spells")
    import_command.add_argument("url")
    import_command.set_defaults(func=cmd_import_class)

    summon = commands.add_parser("build-summon-index", help="build all 18 d20srd summon lists and required monsters")
    summon.set_defaults(func=cmd_build_summon)

    validate = commands.add_parser("validate-indexes", help="validate local canonical indexes")
    validate.set_defaults(func=cmd_validate)

    listing = commands.add_parser("list-spells", help="list canonical spells")
    listing.add_argument("--prefix")
    listing.add_argument("--limit", type=int, default=100)
    listing.set_defaults(func=cmd_list_spells)

    render = commands.add_parser("render-spellbook", help="reassemble a full PDF from current TOC and immutable segments")
    render.add_argument("spellbook_id")
    render.set_defaults(func=cmd_render)

    verify = commands.add_parser("verify-append-only", help="verify segments, append exports, numbering, and full PDF")
    verify.add_argument("spellbook_id")
    verify.set_defaults(func=cmd_verify)

    serve = commands.add_parser("serve", help="run the localhost browser app")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=cmd_serve)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

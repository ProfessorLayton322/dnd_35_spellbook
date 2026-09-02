from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalized_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).replace("’", "'").replace("‘", "'")
    value = "".join(c for c in value if not unicodedata.combining(c)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def slugify(value: str) -> str:
    return normalized_name(value).replace(" ", "-") or "item"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def atomic_json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def portable_relative_path(path: Path, root: Path) -> str:
    """Serialize a relative filesystem path with platform-neutral separators."""

    return path.relative_to(root).as_posix()


def resolve_portable_path(root: Path, stored_path: str) -> Path:
    """Resolve state written on POSIX or Windows beneath ``root`` safely."""

    normalized = str(stored_path).replace("\\", "/")
    relative = PurePosixPath(normalized)
    if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError(f"Invalid runtime-relative path: {stored_path}")
    # A drive prefix remains a normal PurePosixPath component, so reject it
    # explicitly before joining on POSIX hosts.
    if relative.parts[0].endswith(":"):
        raise ValueError(f"Invalid runtime-relative path: {stored_path}")
    return root.joinpath(*relative.parts)


def default_runtime_dir() -> Path:
    return Path(os.environ.get("DND_SPELLBOOK_RUNTIME", "runtime")).expanduser().resolve()

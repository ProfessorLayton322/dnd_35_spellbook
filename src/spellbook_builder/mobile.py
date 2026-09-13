"""Android entry points, called from the app's Java code through Chaquopy."""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from pathlib import Path

from .server import DEFAULT_PORT, LocalServer


_lock = threading.Lock()
_server: LocalServer | None = None


def start(runtime_dir: str, cache_dir: str, preferred_port: int = DEFAULT_PORT) -> int:
    """Start the local server if it is not running and return its port."""

    global _server
    with _lock:
        if _server is None or not _server.running:
            logging.basicConfig(level=logging.INFO)
            os.environ["TMPDIR"] = cache_dir
            tempfile.tempdir = None
            server = LocalServer(Path(runtime_dir), port=preferred_port)
            try:
                server.start()
            except BaseException:
                server.stop()
                raise
            _server = server
        return _server.port


def stop() -> None:
    global _server
    with _lock:
        if _server is not None:
            _server.stop()
            _server = None

"""Serve the web app from a background thread for the packaged desktop and Android apps."""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8735

log = logging.getLogger(__name__)


def bind_local_socket(host: str = DEFAULT_HOST, preferred_port: int = DEFAULT_PORT) -> socket.socket:
    """Bind the preferred port, or any free port when it is taken.

    Binding here and handing the socket to uvicorn avoids a race between
    probing a port and the server opening it. ``SO_REUSEADDR`` is deliberately
    not enabled: on Windows and some Linux configurations it can let two local
    processes bind the same port, which defeats the occupied-port fallback.
    """

    for port in dict.fromkeys((preferred_port, 0)):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((host, port))
        except OSError:
            sock.close()
            continue
        return sock
    raise OSError(f"Could not open a local port on {host}")


class LocalServer:
    """The spellbook web app served on a loopback port by a background thread."""

    def __init__(
        self,
        runtime_dir: Path,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        configure_app: Callable[[FastAPI], None] | None = None,
    ):
        import uvicorn

        from .web import create_app

        self.app = create_app(runtime_dir)
        if configure_app:
            configure_app(self.app)
        self._socket = bind_local_socket(host, port)
        self.host, self.port = self._socket.getsockname()[:2]
        config = uvicorn.Config(self.app, log_level="info", access_log=False, timeout_graceful_shutdown=10)
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._run, name="spellbook-server", daemon=True)
        self.error: BaseException | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def _run(self) -> None:
        try:
            self._server.run(sockets=[self._socket])
        except BaseException as exc:  # reported by start() and the logs
            self.error = exc
            log.exception("Spellbook server stopped unexpectedly")
        finally:
            self._socket.close()

    def start(self, timeout: float = 60.0) -> None:
        self._thread.start()
        deadline = time.monotonic() + timeout
        while not self._server.started:
            if not self._thread.is_alive():
                raise RuntimeError(f"The spellbook server stopped while starting: {self.error or 'unknown error'}")
            if time.monotonic() > deadline:
                raise RuntimeError("The spellbook server did not start in time")
            time.sleep(0.05)
        log.info("Spellbook server running at %s", self.url)

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    def stop(self, timeout: float = 15.0) -> None:
        self._server.should_exit = True
        if self._thread.is_alive():
            self._thread.join(timeout)

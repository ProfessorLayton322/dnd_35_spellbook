"""Desktop app: run the local server and show it in an app window or the web browser.

The Windows build starts this with ``pythonw.exe -m spellbook_builder.desktop``.
It also runs from a development checkout; without a usable app window it
falls back to the web browser.
"""

from __future__ import annotations

import argparse
import faulthandler
import json
import logging
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .server import DEFAULT_PORT, LocalServer


APP_NAME = "D&D 3.5 Spellbook"
APP_DIR_NAME = "DnD35Spellbook"
# The Windows installer looks for this mutex to ask the user to close the app first.
WINDOWS_MUTEX_NAME = "Local\\DnD35SpellbookRunning"
ICON_PATH = Path(__file__).resolve().parent / "static" / "icon.ico"

log = logging.getLogger(__name__)

BROWSER_NOTICE_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>D&amp;D 3.5 Spellbook</title>
<style>
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #f4f3ef; color: #202124; font: 16px/1.5 system-ui, sans-serif; }
  main { max-width: 30rem; padding: 2rem; text-align: center; }
  h1 { font-size: 1.4rem; margin: 0 0 .75rem; }
  p { margin: 0 0 1.5rem; }
  button { font: inherit; padding: .6rem 1rem; margin: .25rem; border-radius: 6px; border: 1px solid #26364a; background: white; cursor: pointer; }
  button.primary { background: #26364a; color: white; }
</style></head>
<body><main>
  <h1>Spellbook is open in your web browser</h1>
  <p>Keep this window open while you use it there. Closing this window quits the Spellbook.</p>
  <button class="primary" onclick="window.pywebview.api.show_app()">Use it in this window</button>
  <button onclick="window.pywebview.api.open_in_browser()">Open the browser again</button>
</main></body></html>
"""


def app_home() -> Path:
    """Per-user folder for the desktop app's data, logs, and instance files."""

    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_DIR_NAME


def default_runtime_dir(home: Path) -> Path:
    override = os.environ.get("DND_SPELLBOOK_RUNTIME")
    return Path(override).expanduser().resolve() if override else home / "runtime"


class InstanceLock:
    """A file lock held while one desktop instance runs; the OS drops it if the process dies."""

    def __init__(self, path: Path):
        self.path = path
        self._handle = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        if sys.platform == "win32":
            import msvcrt

            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        self._handle.close()
        self._handle = None


def _instance_info_path(home: Path) -> Path:
    return home / "instance.json"


def write_instance_info(home: Path, url: str, token: str) -> None:
    _instance_info_path(home).write_text(json.dumps({"url": url, "token": token}), encoding="utf-8")


def activate_running_instance(home: Path, mode: str, wait: float = 20.0) -> bool:
    """Ask the instance holding the lock to show its window or open the browser."""

    # Loopback requests must not go through a system-wide proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + wait
    while True:
        try:
            info = json.loads(_instance_info_path(home).read_text(encoding="utf-8"))
            request = urllib.request.Request(
                f"{info['url']}desktop/activate?mode={mode}",
                data=b"",
                method="POST",
                headers={"X-Spellbook-Token": info["token"]},
            )
            with opener.open(request, timeout=5) as response:
                return response.status == 200
        except Exception as exc:
            # The other instance may still be starting its server.
            if time.monotonic() > deadline:
                log.warning("Could not reach the running instance: %s", exc)
                return False
            time.sleep(0.5)


class DesktopApp:
    def __init__(self, home: Path, runtime_dir: Path, port: int = DEFAULT_PORT):
        self.home = home
        self.token = secrets.token_urlsafe(32)
        self.server = LocalServer(runtime_dir, port=port, configure_app=self._add_routes)
        self.window = None

    def _add_routes(self, app: FastAPI) -> None:
        def activate_route(request: Request):
            if not secrets.compare_digest(request.headers.get("x-spellbook-token", ""), self.token):
                return JSONResponse({"error": "Forbidden"}, status_code=403)
            self.activate("browser" if request.query_params.get("mode") == "browser" else "app")
            return JSONResponse({"ok": True})

        app.add_api_route("/desktop/activate", activate_route, methods=["POST"], include_in_schema=False)

    def run(self, mode: str) -> None:
        webview = _load_webview()
        if webview is not None:
            try:
                self._run_window(webview, mode)
                return
            except Exception:
                if self.window is not None and self.window.events.shown.is_set():
                    raise
                log.exception("Could not open the app window; using the web browser instead")
                self.window = None
        self._run_in_browser_only()

    def _run_window(self, webview, mode: str) -> None:
        from webview.menu import Menu, MenuAction, MenuSeparator

        self.window = webview.create_window(
            APP_NAME,
            url=self.server.url if mode == "app" else None,
            html=BROWSER_NOTICE_HTML if mode == "browser" else None,
            js_api=_WindowApi(self),
            width=1040,
            height=720,
            min_size=(420, 360),
            background_color="#f4f3ef",
        )
        menu = [
            Menu(
                "Spellbook",
                [
                    MenuAction("Open in web browser", self.open_in_browser),
                    MenuAction("Show in this window", self.show_app),
                    MenuSeparator(),
                    MenuAction("Open data folder", self.open_data_folder),
                    MenuSeparator(),
                    MenuAction("Quit", self.quit),
                ],
            )
        ]
        webview.start(
            self._after_window_start,
            (mode,),
            menu=menu,
            icon=str(ICON_PATH) if ICON_PATH.is_file() else None,
            storage_path=str(self.home / "webview"),
        )

    def _after_window_start(self, mode: str) -> None:
        if mode == "browser":
            webbrowser.open(self.server.url)

    def _run_in_browser_only(self) -> None:
        webbrowser.open(self.server.url)
        if sys.platform == "win32":
            _message_box(
                f"{APP_NAME} is running in your web browser:\n{self.server.url}\n\n"
                f"Keep this message open while you use it. Click OK to quit {APP_NAME}."
            )
        else:
            print(f"{APP_NAME} is running at {self.server.url} (press Ctrl+C to quit)", flush=True)
            try:
                threading.Event().wait()
            except KeyboardInterrupt:
                pass

    def open_in_browser(self) -> None:
        url = self.server.url
        if self.window is not None:
            current = self.window.get_current_url() or ""
            if current.startswith(self.server.url):
                url = current
            self.window.load_html(BROWSER_NOTICE_HTML)
        webbrowser.open(url)

    def show_app(self) -> None:
        if self.window is None:
            webbrowser.open(self.server.url)
            return
        if not (self.window.get_current_url() or "").startswith(self.server.url):
            self.window.load_url(self.server.url)
        self.window.restore()
        self.window.show()
        # Toggling "always on top" brings the window in front of other apps.
        self.window.on_top = True
        self.window.on_top = False

    def activate(self, mode: str) -> None:
        if mode == "browser" or self.window is None:
            self.open_in_browser()
        else:
            self.show_app()

    def open_data_folder(self) -> None:
        if sys.platform == "win32":
            os.startfile(self.home)  # noqa: S606 - opens Explorer on the app's own folder
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(self.home)])

    def quit(self) -> None:
        if self.window is not None:
            self.window.destroy()


class _WindowApi:
    """Functions the browser notice calls through ``window.pywebview.api``."""

    def __init__(self, app: DesktopApp):
        self._app = app

    def show_app(self) -> None:
        self._app.show_app()

    def open_in_browser(self) -> None:
        self._app.open_in_browser()


def _load_webview():
    """Import pywebview when a modern web engine can back its window."""

    try:
        if sys.platform == "win32":
            _point_pythonnet_at_this_python()
        import webview

        if sys.platform == "win32":
            from webview.platforms import winforms

            # Without the WebView2 runtime pywebview would fall back to Internet
            # Explorer's engine, which cannot run the app's JavaScript.
            if winforms.renderer != "edgechromium":
                log.warning("Microsoft Edge WebView2 is not installed; using the web browser")
                return None
        return webview
    except Exception:
        log.exception("The app window is not available; using the web browser")
        return None


def _point_pythonnet_at_this_python() -> None:
    import ctypes

    if "PYTHONNET_PYDLL" in os.environ:
        return
    buffer = ctypes.create_unicode_buffer(32768)
    get_module_file_name = ctypes.windll.kernel32.GetModuleFileNameW
    get_module_file_name.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32]
    if get_module_file_name(sys.dllhandle, buffer, len(buffer)):
        os.environ["PYTHONNET_PYDLL"] = buffer.value


def _message_box(text: str, error: bool = False) -> None:
    import ctypes

    icon = 0x10 if error else 0x40  # MB_ICONERROR / MB_ICONINFORMATION
    ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, icon | 0x10000)  # MB_SETFOREGROUND


def show_error(text: str) -> None:
    log.error(text)
    if sys.platform == "win32":
        _message_box(text, error=True)
    else:
        print(text, file=sys.stderr)


def _hold_windows_mutex():
    import ctypes

    create_mutex = ctypes.windll.kernel32.CreateMutexW
    create_mutex.restype = ctypes.c_void_p
    return create_mutex(None, False, WINDOWS_MUTEX_NAME)


def _configure_logging(home: Path) -> Path:
    log_dir = home / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "spellbook.log"
    if path.exists() and path.stat().st_size > 1_000_000:
        try:
            path.replace(log_dir / "spellbook.old.log")
        except OSError:
            pass  # A running instance holds the log open on Windows; rotate next time.
    if sys.stdout is None or sys.stderr is None:
        # pythonw.exe has no console, so everything goes to the log file.
        stream = path.open("a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream
        faulthandler.enable(stream)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spellbook-desktop", description=f"Run {APP_NAME} in an app window or your web browser")
    parser.add_argument("--browser", action="store_true", help="use the web browser instead of an app window")
    parser.add_argument("--runtime", help="runtime/user-data directory (default: per-user app data or DND_SPELLBOOK_RUNTIME)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="preferred local port")
    args = parser.parse_args(argv)
    mode = "browser" if args.browser else "app"

    home = app_home()
    log_path = _configure_logging(home)
    lock = InstanceLock(home / "instance.lock")
    if not lock.acquire():
        if activate_running_instance(home, mode):
            return 0
        show_error(f"{APP_NAME} is already running but is not responding.\n\nClose it, or restart your computer, and try again.")
        return 1
    try:
        _mutex = _hold_windows_mutex() if sys.platform == "win32" else None
        runtime_dir = Path(args.runtime).expanduser().resolve() if args.runtime else default_runtime_dir(home)
        try:
            app = DesktopApp(home, runtime_dir, args.port)
            app.server.start()
        except Exception as exc:
            log.exception("Startup failed")
            show_error(f"{APP_NAME} could not start:\n\n{exc}\n\nDetails are in {log_path}")
            return 1
        write_instance_info(home, app.server.url, app.token)
        try:
            app.run(mode)
        finally:
            app.server.stop()
    finally:
        _instance_info_path(home).unlink(missing_ok=True)
        lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

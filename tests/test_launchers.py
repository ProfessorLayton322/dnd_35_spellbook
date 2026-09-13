import json
import tempfile
import urllib.request

import pytest

from spellbook_builder import desktop, mobile
from spellbook_builder.server import LocalServer, bind_local_socket


def fetch(url: str) -> int:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=10) as response:
        response.read()
        return response.status


def test_local_server_serves_the_app_until_stopped(tmp_path):
    server = LocalServer(tmp_path / "runtime", port=0)
    server.start()
    try:
        assert server.url.startswith("http://127.0.0.1:")
        assert fetch(server.url) == 200
    finally:
        server.stop()

    assert not server.running
    with pytest.raises(OSError):
        fetch(server.url)


def test_taken_preferred_port_falls_back_to_a_free_port():
    taken = bind_local_socket(preferred_port=0)
    taken.listen()
    try:
        port = taken.getsockname()[1]
        fallback = bind_local_socket(preferred_port=port)
        try:
            assert fallback.getsockname()[1] not in {0, port}
        finally:
            fallback.close()
    finally:
        taken.close()


def test_mobile_start_reuses_the_running_server_until_stopped(tmp_path, monkeypatch):
    # mobile.start points temporary files at the Android cache directory.
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", None)
    cache = tmp_path / "cache"
    cache.mkdir()

    port = mobile.start(str(tmp_path / "runtime"), str(cache), 0)
    try:
        assert mobile.start(str(tmp_path / "runtime"), str(cache), 0) == port
        assert fetch(f"http://127.0.0.1:{port}/") == 200
    finally:
        mobile.stop()

    with pytest.raises(OSError):
        fetch(f"http://127.0.0.1:{port}/")


def test_instance_lock_has_one_holder(tmp_path):
    first = desktop.InstanceLock(tmp_path / "instance.lock")
    second = desktop.InstanceLock(tmp_path / "instance.lock")

    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()


def test_second_launch_activates_the_running_instance(tmp_path, monkeypatch):
    app = desktop.DesktopApp(tmp_path, tmp_path / "runtime", port=0)
    modes = []
    monkeypatch.setattr(app, "activate", modes.append)
    app.server.start()
    try:
        desktop.write_instance_info(tmp_path, app.server.url, app.token)
        assert desktop.activate_running_instance(tmp_path, "browser")
        assert desktop.activate_running_instance(tmp_path, "app")

        desktop.write_instance_info(tmp_path, app.server.url, "wrong-token")
        assert not desktop.activate_running_instance(tmp_path, "app", wait=0)
    finally:
        app.server.stop()

    assert modes == ["browser", "app"]


def test_desktop_main_serves_while_running_and_cleans_up(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "app_home", lambda: tmp_path)
    monkeypatch.setattr(desktop, "_load_webview", lambda: None)
    seen = {}

    def run_in_browser_only(app):
        seen["status"] = fetch(app.server.url)
        seen["info"] = json.loads((tmp_path / "instance.json").read_text(encoding="utf-8"))
        seen["locked"] = not desktop.InstanceLock(tmp_path / "instance.lock").acquire()

    monkeypatch.setattr(desktop.DesktopApp, "_run_in_browser_only", run_in_browser_only)

    assert desktop.main(["--port", "0", "--runtime", str(tmp_path / "runtime")]) == 0

    assert seen["status"] == 200
    assert seen["locked"]
    assert seen["info"]["url"].startswith("http://127.0.0.1:")
    assert not (tmp_path / "instance.json").exists()
    assert (tmp_path / "runtime" / "state.json").is_file()
    assert desktop.InstanceLock(tmp_path / "instance.lock").acquire()

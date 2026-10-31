"""The workspaces of the sandbox: the idle sweep and the disk budget."""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from server import Service, make_server


@pytest.fixture
def api(tmp_path):
    server = make_server("127.0.0.1", 0, str(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    yield base
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def request(api, method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(api + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if raw else {})


def make_workspace(root, name, age_seconds=0):
    path = os.path.join(str(root), name)
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "file"), "w") as handle:
        handle.write("hello")
    if age_seconds:
        stamp = time.time() - age_seconds
        os.utime(os.path.join(path, "file"), (stamp, stamp))
        os.utime(path, (stamp, stamp))
    return path


def test_the_cleanup_removes_idle_workspaces_and_keeps_the_recent_ones(tmp_path, monkeypatch):
    monkeypatch.setenv("HUDDLE_WORKSPACE_TTL_HOURS", "24")
    service = Service(str(tmp_path))
    old = make_workspace(tmp_path, "old", age_seconds=48 * 3600)
    recent = make_workspace(tmp_path, "recent")
    removed = service.cleanup_workspaces()
    assert removed == ["old"]
    assert not os.path.exists(old)
    assert os.path.exists(recent)


def test_the_cleanup_can_be_switched_off(tmp_path, monkeypatch):
    monkeypatch.setenv("HUDDLE_WORKSPACE_TTL_HOURS", "0")
    service = Service(str(tmp_path))
    old = make_workspace(tmp_path, "old", age_seconds=48 * 3600)
    assert service.cleanup_workspaces() == []
    assert os.path.exists(old)


def test_the_cleanup_skips_workspaces_with_running_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("HUDDLE_WORKSPACE_TTL_HOURS", "1")
    service = Service(str(tmp_path))
    busy = make_workspace(tmp_path, "busy", age_seconds=48 * 3600)
    idle = make_workspace(tmp_path, "idle", age_seconds=48 * 3600)
    service.running["busy"] = {4242}
    removed = service.cleanup_workspaces()
    assert removed == ["idle"]
    assert os.path.exists(busy)
    assert not os.path.exists(idle)


def test_a_workspace_over_its_disk_budget_refuses_writes(api, monkeypatch):
    monkeypatch.setenv("HUDDLE_WORKSPACE_MAX_MB", "1")
    status, _ = request(api, "PUT", "/file",
                         {"workspace": "room", "path": "big.txt", "content": "a" * (1024 * 1024)})
    assert status == 200
    status, body = request(api, "PUT", "/file",
                           {"workspace": "room", "path": "more.txt", "content": "b"})
    assert status == 413
    assert body["error"] == "workspace is full (1 MB); delete files to continue"


def test_a_workspace_over_its_disk_budget_is_reported_on_stderr(api, monkeypatch):
    monkeypatch.setenv("HUDDLE_WORKSPACE_MAX_MB", "1")
    request(api, "PUT", "/file",
            {"workspace": "room", "path": "big.txt", "content": "a" * (1024 * 1024)})
    request(api, "PUT", "/file", {"workspace": "room", "path": "more.txt", "content": "b"})
    status, body = request(api, "POST", "/exec", {"workspace": "room", "cmd": "echo hi"})
    assert status == 200
    assert "workspace is full (1 MB); delete files to continue" in body["stderr"]
    assert body["stdout"] == "hi\n"


def test_a_workspace_under_its_disk_budget_is_not_complained_about(api, monkeypatch):
    monkeypatch.setenv("HUDDLE_WORKSPACE_MAX_MB", "50")
    status, body = request(api, "POST", "/exec", {"workspace": "room", "cmd": "echo hi"})
    assert status == 200
    assert "workspace is full" not in body["stderr"]

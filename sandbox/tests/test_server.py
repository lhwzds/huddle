import base64
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from server import make_server


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
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def post(api, path, **body):
    status, result = request(api, "POST", path, body)
    assert status == 200, result
    return result


def query(endpoint, **params):
    return endpoint + "?" + urllib.parse.urlencode(params)


def test_exec_echo_exit_stderr_home_and_workspace_isolation(api):
    r = post(api, "/exec", workspace="room", cmd="printf '%s' \\\"$HOME\\\"; echo err >&2; exit 7")
    assert "/room" in r["stdout"] and r["stderr"].strip() == "err" and r["exit_code"] == 7

    post(api, "/exec", workspace="room", cmd="echo persisted > file")
    post(api, "/exec", workspace="other", cmd="echo private > file")
    _, result = request(api, "GET", query("/file", workspace="room", path="file"))
    assert result["content"] == "persisted\n"
    _, listing = request(api, "GET", query("/files", workspace="room"))
    assert [x["path"] for x in listing] == ["file"]


def test_timeout_kills_process_group_and_child(api):
    for cmd in ("sleep 30", "bash -c 'sleep 30 & sleep 30'"):
        start = time.monotonic()
        result = post(api, "/exec", workspace="timeout", cmd=cmd, timeout_s=1)
        assert result["timed_out"] is True
        assert time.monotonic() - start < 2.5


def test_kill_running_exec(api):
    result = {}
    t = threading.Thread(target=lambda: result.setdefault("exec", post(api, "/exec", workspace="killme", cmd="sleep 30")))
    t.start()
    time.sleep(.3)
    killed = post(api, "/kill", workspace="killme")
    assert killed["killed"] >= 1
    t.join(timeout=3)
    assert not t.is_alive()
    assert result["exec"]["timed_out"] is False
    assert result["exec"]["exit_code"] != 0


def test_output_truncation(api):
    r = post(api, "/exec", workspace="out", cmd="python3 -c 'print(\"x\"*13000)'")
    assert len(r["stdout"]) == 12000 and r["truncated"]


def test_paths_bad_workspace_and_symlink(api, tmp_path):
    for path in ("../x", "/etc/passwd"):
        status, _ = request(api, "GET", query("/file", workspace="w", path=path))
        assert status == 400
    (tmp_path / "w").mkdir(exist_ok=True)
    (tmp_path / "w" / "escape").symlink_to("/etc", target_is_directory=True)
    status, _ = request(api, "GET", query("/file", workspace="w", path="escape/passwd"))
    assert status == 400
    assert request(api, "GET", query("/files", workspace="bad/name"))[0] == 400


def test_write_read_hidden_listing_and_binary(api):
    status, value = request(api, "PUT", "/file", {"workspace": "files", "path": "a/b.txt", "content": "hello"})
    assert status == 200 and value["size"] == 5
    _, got = request(api, "GET", query("/file", workspace="files", path="a/b.txt"))
    assert got == {"path": "a/b.txt", "content": "hello", "binary": False}
    request(api, "PUT", "/file", {"workspace": "files", "path": ".secret", "content": "x"})
    request(api, "PUT", "/file", {"workspace": "files", "path": "blob", "content": "ÿ"})
    # UTF-8 text written through the API remains text; make genuinely binary via exec.
    post(api, "/exec", workspace="files", cmd="printf '\\377' > raw")
    _, listing = request(api, "GET", query("/files", workspace="files"))
    assert all(not x["path"].startswith(".") for x in listing)
    _, binary = request(api, "GET", query("/file", workspace="files", path="raw"))
    assert binary["binary"] and base64.b64decode(binary["content"]) == b"\xff"

@pytest.mark.parametrize("cmd", [
    "yes | head -c 50000000",
    "yes",
    "yes >&2",
])
def test_unbounded_output_is_stopped_quickly(api, cmd):
    start = time.monotonic()
    result = post(api, "/exec", workspace="flood", cmd=cmd, timeout_s=10)
    assert time.monotonic() - start < 3
    assert result["truncated"] is True
    assert result["timed_out"] is False
    key = "stderr" if cmd == "yes >&2" else "stdout"
    assert len(result[key]) == 12_000


def test_timeout_bounds_output_pipe_held_by_background_child(api):
    start = time.monotonic()
    result = post(api, "/exec", workspace="background-timeout",
                  cmd="sleep 20 & echo hi", timeout_s=1)
    assert time.monotonic() - start < 2
    assert result["timed_out"] is True
    assert result["stdout"] == "hi\n"


def test_timeout_after_output_truncation(api):
    start = time.monotonic()
    result = post(api, "/exec", workspace="timeout-output",
                  cmd="head -c 12001 /dev/zero | tr '\\0' x; sleep 5", timeout_s=1)
    assert time.monotonic() - start < 2
    assert result["timed_out"] is True
    assert result["truncated"] is True
    assert len(result["stdout"]) == 12_000


def test_fork_bomb_is_killed_and_the_service_survives(api, monkeypatch):
    """A command that keeps forking must not take the sandbox down."""
    monkeypatch.setenv("HUDDLE_SANDBOX_DEBUG", "1")
    start = time.monotonic()
    result = post(api, "/exec", workspace="bomb",
                  cmd="while true; do sleep 100 & done", timeout_s=2)
    elapsed = time.monotonic() - start
    assert result["timed_out"] is True, result
    assert elapsed < 6, "the sandbox took %.1fs to stop the fork bomb" % elapsed
    pgid = result["pgid"]
    # every process of the bomb is gone, the group does not exist any more
    with pytest.raises(ProcessLookupError):
        os.killpg(pgid, 0)
    # and the sandbox still works
    follow = post(api, "/exec", workspace="bomb", cmd="echo ok")
    assert follow["stdout"] == "ok\n" and follow["exit_code"] == 0


def test_exec_retries_when_forking_fails(api, monkeypatch):
    import errno

    import server as server_module

    real_popen = server_module.subprocess.Popen
    calls = []

    def flaky_popen(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable")
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(server_module.subprocess, "Popen", flaky_popen)
    result = post(api, "/exec", workspace="retry", cmd="echo ok")
    assert result["stdout"] == "ok\n" and result["exit_code"] == 0
    assert len(calls) == 2


def test_exec_answers_503_when_forking_keeps_failing(api, monkeypatch):
    import errno

    import server as server_module

    def broken_popen(*args, **kwargs):
        raise BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable")

    monkeypatch.setattr(server_module.subprocess, "Popen", broken_popen)
    status, body = request(api, "POST", "/exec",
                            {"workspace": "busy", "cmd": "echo ok"})
    assert status == 503 and body == {"error": "busy"}
    # the sandbox is still usable afterwards
    monkeypatch.undo()
    assert post(api, "/exec", workspace="busy", cmd="echo ok")["stdout"] == "ok\n"


def _fake_proc(root, entries):
    """Write fake /proc/<pid>/stat files: (pid, comm, state, ppid, pgrp, sid)."""
    proc = root / "proc"
    for pid, comm, state, ppid, pgrp, sid in entries:
        entry = proc / str(pid)
        entry.mkdir(parents=True, exist_ok=True)
        (entry / "stat").write_text(
            "%d (%s) %s %d %d %d 0 0 0 0 0 0\n" % (pid, comm, state, ppid, pgrp, sid))
    return proc


def test_session_scan_finds_every_process_of_a_session(tmp_path, monkeypatch):
    import server as server_module

    proc = _fake_proc(tmp_path, [
        (11, "bash", "S", 1, 11, 11),
        (12, "sleep", "S", 11, 11, 11),
        (13, "other", "S", 1, 13, 13),
        (14, "gone", "Z", 11, 11, 11),
    ])
    monkeypatch.setattr(server_module, "PROC", str(proc))
    assert dict(server_module.session_processes(11)) == {11: "S", 12: "S", 14: "Z"}
    assert dict(server_module.session_processes(13)) == {13: "S"}


def test_kill_group_sweeps_the_whole_session(tmp_path, monkeypatch):
    import server as server_module

    proc = _fake_proc(tmp_path, [
        (21, "bash", "S", 1, 21, 21),
        (22, "sleeper", "S", 21, 21, 21),
    ])
    monkeypatch.setattr(server_module, "PROC", str(proc))
    killed = []

    def fake_kill(pid, sig):
        killed.append(pid)
        # a killed process stays behind as a zombie
        (proc / str(pid) / "stat").write_text(
            "%d (x) Z 1 %d %d 0 0 0 0 0 0\n" % (pid, pid, pid))

    monkeypatch.setattr(server_module.os, "kill", fake_kill)
    server_module.kill_group(999999, 21)
    assert set(killed) == {21, 22}


def test_kill_exceeded_kills_the_groups_of_workspaces_that_timed_out(tmp_path, monkeypatch):
    import server as server_module

    service = server_module.Service(str(tmp_path))
    service.register("room", 4242)
    service.mark_exceeded("room", 4242)
    service.register("other", 4343)

    killed = []
    monkeypatch.setattr(server_module, "kill_group",
                        lambda pgid, sid=None, reap=None, timeout=0: killed.append((pgid, sid)))
    service.kill_exceeded()
    assert killed == [(4242, 4242)]
    assert service.exceeded["room"] == set()


def test_sweep_kills_processes_that_left_their_command(tmp_path, monkeypatch):
    import server as server_module

    service = server_module.Service(str(tmp_path / "work"))
    me, my_session = os.getpid(), os.getsid(0)
    proc = _fake_proc(tmp_path, [
        (1, "tini", "S", 0, 1, 1),
        (me, "python", "S", 1, my_session, my_session),
        (500, "bash", "S", me, 500, 500),        # a running command
        (501, "sleep", "S", 500, 501, 500),      # its child, in another group but the same session
        (600, "miner", "S", 1, 600, 600),        # left the session with setsid()
        (700, "gone", "Z", 1, 700, 700),         # already dead
    ])
    monkeypatch.setattr(server_module, "PROC", str(proc))
    service.procs[500] = object()
    killed = []
    monkeypatch.setattr(server_module.os, "kill", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(service, "reap_now", lambda: None)

    assert service.sweep_detached() == 0  # off unless the container switches it on
    monkeypatch.setenv("HUDDLE_SANDBOX_SWEEP", "1")
    assert service.sweep_detached() == 1
    assert killed == [600]


def test_a_command_that_keeps_writing_is_stopped_at_the_disk_budget(api, monkeypatch):
    monkeypatch.setenv("HUDDLE_WORKSPACE_MAX_MB", "1")
    started = time.monotonic()
    out = post(api, "/exec", workspace="disk", timeout_s=30,
               cmd="mkdir -p .cache; while true; do head -c 262144 /dev/zero >> .cache/fill; sleep 0.05; done")
    assert time.monotonic() - started < 10
    assert out["timed_out"] is False
    assert "workspace is full" in out["stderr"]

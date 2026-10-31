#!/usr/bin/env python3
"""A small, process-isolated HTTP shell service for Huddle agents."""
import base64
import codecs
import errno
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

MAX_FILE = 1024 * 1024
MAX_OUTPUT = 12_000
MAX_LISTING = 500
WORKSPACE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

TTL_HOURS = 24.0       # how long an idle workspace is kept
MAX_MB = 50.0          # disk budget of one workspace
CLEAN_INTERVAL = 600.0 # how often the idle workspaces are swept
SWEEP_INTERVAL = 5.0   # how often processes that left their command are looked for
DISK_POLL = 1.0        # how often a running command's workspace is measured
KILL_INTERVAL = 0.02   # pause between two kill attempts
KILL_TIMEOUT = 3.0     # upper bound for killing one process group
REAP_INTERVAL = 0.2    # pause between two reaper passes
WAIT_POLL = 0.01       # pause between two waitpid polls
REAP_KEEP = 30.0       # how long the status of a reaped child is remembered
PROC = "/proc"


class Busy(Exception):
    """The sandbox could not fork another process."""


def env_number(name, default):
    """A number from the environment, or the default when it is not set."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        return default
    return value


def ttl_hours():
    """Hours an idle workspace is kept; 0 switches the cleanup off."""
    return max(0.0, env_number("HUDDLE_WORKSPACE_TTL_HOURS", TTL_HOURS))


def max_mb():
    """The disk budget of one workspace, in megabytes."""
    return max(0.0, env_number("HUDDLE_WORKSPACE_MAX_MB", MAX_MB))


def sweep_enabled():
    """True in the sandbox container, where every process belongs to the server or a command.

    Off by default so that tests and a server started by hand never touch other processes.
    """
    return os.environ.get("HUDDLE_SANDBOX_SWEEP", "").strip() == "1"


def debug_enabled():
    """Extra fields in the answer, used by the tests."""
    flag = os.environ.get("HUDDLE_SANDBOX_DEBUG", "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def fork_failed(exc):
    """True when an OSError means "the process table is full"."""
    if isinstance(exc, BlockingIOError):
        return True
    return exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK)


def decode_status(status):
    """Raw wait status -> exit code (negative when the child was signalled)."""
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return -os.WTERMSIG(status)
    return -1


def proc_stat(pid):
    """(state, ppid, pgrp, session) of a pid, read from /proc (Linux only)."""
    try:
        with open("%s/%d/stat" % (PROC, pid), "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    end = data.rfind(b")")
    if end < 0:
        return None
    fields = data[end + 1:].split()
    if len(fields) < 4:
        return None
    try:
        state = fields[0].decode("ascii", "replace")
        return state, int(fields[1]), int(fields[2]), int(fields[3])
    except ValueError:
        return None


def session_processes(sid):
    """(pid, state) of every process that belongs to session sid."""
    found = []
    try:
        entries = os.listdir(PROC)
    except OSError:
        return found
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        info = proc_stat(pid)
        if info is not None and info[3] == sid:
            found.append((pid, info[0]))
    return found


def kill_group(pgid, sid=None, reap=None, timeout=KILL_TIMEOUT):
    """Kill a process group until it is really gone.

    A single SIGKILL races with the processes that are still forking: the
    survivors keep the process table full. So the group is signalled again and
    again until the kernel reports that it does not exist any more. On Linux
    the whole session is swept as well, because a child can leave the process
    group of its parent but never leaves the session.
    """
    if sid is None:
        sid = pgid
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            break
        except PermissionError:
            # Only zombies (or processes of another user) are left, they cannot
            # be signalled any more; the reaper takes care of our own ones.
            pass
        except OSError:
            break
        if reap is not None:
            reap()
        if time.monotonic() >= deadline:
            break
        time.sleep(KILL_INTERVAL)
    if not os.path.isdir(PROC):
        return  # no /proc (macOS): the process group is all we can do
    while True:
        alive = False
        for pid, state in session_processes(sid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
            if state != "Z":
                alive = True
        if not alive:
            break
        if reap is not None:
            reap()
        if time.monotonic() >= deadline:
            break
        time.sleep(KILL_INTERVAL)


class Reaper:
    """Collects the zombies of the whole process.

    In the container the sandbox runs as pid 1, so the orphaned grandchildren
    of a command become children of the server. Nobody waits for them, so they
    stay behind as zombies until the process table is full. One reaper per
    process collects them. It also keeps the exit status of every child it
    collects, so a command never loses its exit code.
    """

    _instance = None
    _guard = threading.Lock()

    def __init__(self):
        self.lock = threading.RLock()
        self.reaped = {}  # pid -> (raw status, timestamp)
        self.live = set()  # pids of the commands that are still running
        self.users = 0
        self.stopping = threading.Event()
        self.thread = None

    @classmethod
    def instance(cls):
        with cls._guard:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def acquire(self):
        with self.lock:
            self.users += 1
            if self.thread is None or not self.thread.is_alive():
                self.stopping.clear()
                self.thread = threading.Thread(target=self._loop, daemon=True)
                self.thread.start()

    def release(self):
        with self.lock:
            self.users = max(0, self.users - 1)
            if self.users:
                return
            self.stopping.set()
            thread, self.thread = self.thread, None
        if thread is not None:
            thread.join(timeout=2)

    def _loop(self):
        while not self.stopping.is_set():
            self.reap_now()
            self.stopping.wait(REAP_INTERVAL)

    def reap_now(self):
        """Reap every child that exited and remember its status."""
        with self.lock:
            while True:
                try:
                    pid, status = os.waitpid(-1, os.WNOHANG)
                except ChildProcessError:
                    break
                except OSError:
                    break
                if pid == 0:
                    break
                # The status of a command that is still running is kept here,
                # so that the command does not lose its exit code.
                self.reaped[pid] = (status, time.monotonic())
            now = time.monotonic()
            for pid, (_, seen) in list(self.reaped.items()):
                if now - seen > REAP_KEEP and pid not in self.live:
                    del self.reaped[pid]


class Service:
    def __init__(self, work_root):
        self.root = os.path.realpath(work_root)
        os.makedirs(self.root, exist_ok=True)
        self.reaper = Reaper.instance()
        self.lock = self.reaper.lock
        self.running = {}       # workspace -> {process-group ids}
        self.counts = {}        # workspace -> number of running commands
        self.exceeded = {}      # workspace -> {pgid} of commands that timed out
        self.procs = {}         # pid -> Popen, kept so it is never collected
        self._cleaner = None    # the thread that sweeps the idle workspaces
        self._stop = threading.Event()

    def workspace(self, name):
        if not isinstance(name, str) or not WORKSPACE_RE.fullmatch(name):
            raise ValueError("invalid workspace")
        path = os.path.join(self.root, name)
        os.makedirs(path, exist_ok=True)
        real = os.path.realpath(path)
        if os.path.commonpath((self.root, real)) != self.root:
            raise ValueError("invalid workspace")
        return real

    def file_path(self, name, rel):
        work = self.workspace(name)
        if not isinstance(rel, str) or not rel or rel.startswith("/") or ".." in rel.split("/"):
            raise ValueError("invalid path")
        path = os.path.realpath(os.path.join(work, rel))
        if os.path.commonpath((work, path)) != work:
            raise ValueError("invalid path")
        return work, path

    # -- children ----------------------------------------------------------

    def start(self):
        """Start the reaper and the sweep of the idle workspaces."""
        self.reaper.acquire()
        self._stop.clear()
        self._cleaner = threading.Thread(target=self._clean_loop, name="workspace-cleaner",
                                         daemon=True)
        self._cleaner.start()

    def stop(self):
        self._stop.set()
        cleaner = self._cleaner
        if cleaner is not None:
            cleaner.join(timeout=2)
            self._cleaner = None
        self.reaper.release()

    def _clean_loop(self):
        """Kill processes that left their command; delete workspaces nobody touched for a long time."""
        interval = max(1.0, env_number("HUDDLE_WORKSPACE_CLEANUP_INTERVAL_S", CLEAN_INTERVAL))
        tick = min(interval, SWEEP_INTERVAL)
        next_clean = time.monotonic() + interval
        while not self._stop.wait(tick):
            try:
                self.sweep_detached()
                if time.monotonic() >= next_clean:
                    next_clean = time.monotonic() + interval
                    self.cleanup_workspaces()
            except Exception:
                pass

    def sweep_detached(self):
        """Kill every process that is neither the server nor part of a running command.

        A command runs in its own session, and everything it starts stays in that session unless it
        calls setsid(). Such a process would outlive the timeout, Stop and the command itself, so
        only the server's session and the sessions of the running commands are allowed to exist.
        Returns how many processes were killed.
        """
        if not sweep_enabled() or not os.path.isdir(PROC):
            return 0
        killed = 0
        with self.lock:
            keep = {os.getsid(0)} | set(self.procs)
            for entry in os.listdir(PROC):
                if not entry.isdigit():
                    continue
                pid = int(entry)
                if pid in (1, os.getpid()):
                    continue
                info = proc_stat(pid)
                if info is None or info[0] == "Z" or info[3] in keep:
                    continue
                try:
                    os.kill(pid, signal.SIGKILL)
                    killed += 1
                except OSError:
                    pass
        if killed:
            self.reap_now()
        return killed

    # -- workspaces --------------------------------------------------------

    def workspace_size(self, work):
        """The size of all files in a workspace, hidden ones included, in bytes."""
        total = 0
        for root, dirs, files in os.walk(work, followlinks=False):
            for name in files:
                try:
                    st = os.stat(os.path.join(root, name), follow_symlinks=False)
                except OSError:
                    continue
                total += st.st_size
        return total

    def workspace_mtime(self, work):
        """The newest modification time of a workspace and its files."""
        try:
            newest = os.stat(work).st_mtime
        except OSError:
            return time.time()
        for root, dirs, files in os.walk(work, followlinks=False):
            for name in files:
                try:
                    st = os.stat(os.path.join(root, name), follow_symlinks=False)
                except OSError:
                    continue
                newest = max(newest, st.st_mtime)
        return newest

    def busy_workspaces(self):
        """The workspaces that still have a running command."""
        with self.lock:
            names = {name for name, groups in self.running.items() if groups}
            names.update(name for name, count in self.counts.items() if count)
            return names

    def cleanup_workspaces(self, now=None):
        """Delete the workspaces that have been idle for too long.

        A workspace with a running command is never deleted.
        """
        hours = ttl_hours()
        if hours <= 0:
            return []
        now = time.time() if now is None else now
        limit = now - hours * 3600
        busy = self.busy_workspaces()
        removed = []
        try:
            names = os.listdir(self.root)
        except OSError:
            return removed
        for name in names:
            if name in busy:
                continue
            path = os.path.join(self.root, name)
            if not os.path.isdir(path):
                continue
            try:
                if self.workspace_mtime(path) > limit:
                    continue
                shutil.rmtree(path, ignore_errors=True)
            except OSError:
                continue
            removed.append(name)
        return removed

    def over_budget(self, work):
        """True when the workspace uses more than its disk budget."""
        budget = max_mb()
        return budget > 0 and self.workspace_size(work) > budget * 1024 * 1024

    def full_note(self, work):
        """The note to show when the workspace is over its disk budget."""
        if not self.over_budget(work):
            return None
        return "workspace is full (%d MB); delete files to continue" % int(max_mb())

    def reap_now(self):
        self.reaper.reap_now()

    def wait_child(self, pid, timeout=None, since=0.0):
        """Wait for a command to exit. Returns (exit code, timed out).

        The reaper may have collected the status of the command already; it is
        picked up here, so the exit code never gets lost. Statuses that are
        older than the command are ignored, a pid can be reused.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self.lock:
                entry = self.reaper.reaped.get(pid)
                if entry is not None and entry[1] >= since:
                    del self.reaper.reaped[pid]
                    return decode_status(entry[0]), False
                try:
                    waited, status = os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    # Somebody else reaped it and the status is gone.
                    return -1, False
                if waited == pid:
                    return decode_status(status), False
            if deadline is not None and time.monotonic() >= deadline:
                return None, True
            time.sleep(WAIT_POLL)

    # -- commands ----------------------------------------------------------

    def spawn(self, work, cmd):
        """Run a command in its own session. Raises Busy when out of pids."""
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": work,
               "LANG": "C.UTF-8", "TERM": "dumb"}
        args = ["bash", "-c", cmd]
        for _ in range(2):
            with self.lock:
                try:
                    proc = subprocess.Popen(args, cwd=work, env=env,
                                            stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE,
                                            start_new_session=True)
                except OSError as exc:
                    if not fork_failed(exc):
                        raise
                else:
                    # The lock keeps the reaper from taking away the status of
                    # the command before it is known here.
                    self.reaper.live.add(proc.pid)
                    self.procs[proc.pid] = proc
                    return proc
            # The process table is full: get rid of the groups of the
            # commands that already ran into their timeout, then try again.
            self.kill_exceeded()
        raise Busy("busy")

    def mark_exceeded(self, workspace, pgid):
        """Remember that a command of a workspace ran into its timeout."""
        with self.lock:
            self.exceeded.setdefault(workspace, set()).add(pgid)

    def group_alive(self, pgid):
        """True while the process group still exists."""
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True

    def kill_exceeded(self):
        """Kill the process groups of all workspaces that exceeded a timeout."""
        with self.lock:
            groups = []
            for workspace, pgids in self.exceeded.items():
                if not pgids:
                    continue
                groups.extend(self.running.get(workspace, ()))
        for pgid in groups:
            kill_group(pgid, pgid, reap=self.reap_now)
        with self.lock:
            for pgids in self.exceeded.values():
                for pgid in list(pgids):
                    if not self.group_alive(pgid):
                        pgids.discard(pgid)

    def register(self, workspace, pgid):
        with self.lock:
            self.running.setdefault(workspace, set()).add(pgid)

    def unregister(self, workspace, pgid):
        with self.lock:
            self.running.get(workspace, set()).discard(pgid)
            self.reaper.live.discard(pgid)
            self.procs.pop(pgid, None)
            # The mark of a command that timed out is only dropped when its
            # process group is really gone, otherwise the survivors would
            # still fill the process table.
            if not self.group_alive(pgid):
                self.exceeded.get(workspace, set()).discard(pgid)


class Handler(BaseHTTPRequestHandler):
    server_version = "HuddleSandbox/1.0"

    @property
    def service(self):
        return self.server.service

    def log_message(self, fmt, *args):
        super().log_message(fmt, *args)

    def _send(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status, message):
        self._send(status, {"error": str(message)})

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 2 * MAX_FILE + 65536:
                raise ValueError("request body too large")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("JSON object required")
            return data
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("invalid JSON request") from exc

    def _query(self, key):
        vals = parse_qs(urlsplit(self.path).query, keep_blank_values=True).get(key)
        if not vals:
            raise ValueError("missing " + key)
        return vals[0]

    def do_GET(self):
        try:
            url = urlsplit(self.path)
            if url.path == "/health":
                self._send(200, {"ok": True})
            elif url.path == "/files":
                name = self._query("workspace")
                work = self.service.workspace(name)
                found = []
                for root, dirs, files in os.walk(work, followlinks=False):
                    dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                    for filename in sorted(files):
                        if filename.startswith("."):
                            continue
                        full = os.path.join(root, filename)
                        rel = os.path.relpath(full, work).replace(os.sep, "/")
                        try:
                            st = os.stat(full, follow_symlinks=False)
                            if not os.path.isfile(full):
                                continue
                        except OSError:
                            continue
                        found.append({"path": rel, "size": st.st_size, "mtime": float(st.st_mtime)})
                        if len(found) >= MAX_LISTING:
                            break
                    if len(found) >= MAX_LISTING:
                        break
                found.sort(key=lambda x: x["path"])
                self._send(200, found)
            elif url.path == "/file":
                name, rel = self._query("workspace"), self._query("path")
                _, path = self.service.file_path(name, rel)
                try:
                    with open(path, "rb") as f:
                        raw = f.read(MAX_FILE + 1)
                except FileNotFoundError:
                    self._error(404, "file not found"); return
                if len(raw) > MAX_FILE:
                    self._error(400, "file exceeds 1 MB"); return
                try:
                    content = raw.decode("utf-8")
                    binary = False
                except UnicodeDecodeError:
                    content, binary = base64.b64encode(raw).decode("ascii"), True
                self._send(200, {"path": rel, "content": content, "binary": binary})
            else:
                self._error(404, "not found")
        except ValueError as exc:
            self._error(400, exc)
        except Exception:
            self._error(500, "internal server error")

    def do_PUT(self):
        if urlsplit(self.path).path != "/file":
            self._error(404, "not found"); return
        try:
            data = self._body()
            name, rel, content = data.get("workspace"), data.get("path"), data.get("content")
            if not isinstance(content, str):
                raise ValueError("content must be text")
            raw = content.encode("utf-8")
            if len(raw) > MAX_FILE:
                raise ValueError("content exceeds 1 MB")
            work, path = self.service.file_path(name, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            # Revalidate after creating parent directories to defend against symlink escapes.
            _, checked = self.service.file_path(name, rel)
            if checked != path:
                raise ValueError("invalid path")
            with open(path, "wb") as f:
                f.write(raw)
            note = self.service.full_note(work)
            if note:
                self._error(413, note); return
            self._send(200, {"path": rel, "size": len(raw)})
        except ValueError as exc:
            self._error(400, exc)
        except Exception:
            self._error(500, "internal server error")

    def do_POST(self):
        route = urlsplit(self.path).path
        try:
            data = self._body()
            name = data.get("workspace")
            work = self.service.workspace(name)
            if route == "/exec":
                cmd = data.get("cmd")
                if not isinstance(cmd, str):
                    raise ValueError("cmd must be text")
                try:
                    timeout = float(data.get("timeout_s", 10))
                except (TypeError, ValueError):
                    raise ValueError("invalid timeout_s")
                timeout = min(60, max(1, timeout))
                with self.service.lock:
                    if self.service.counts.get(name, 0) >= 4:
                        self._error(429, "too many concurrent executions"); return
                    self.service.counts[name] = self.service.counts.get(name, 0) + 1
                proc = None
                pgid = None
                timed_out = False
                code = None
                since = time.monotonic()
                finished = threading.Event()  # stops the disk watcher
                watch_lock = threading.Lock()  # the watcher signals only while finished is unset
                disk_watcher = None

                def terminate():
                    """Kill the whole process group until it is really gone."""
                    if pgid is not None:
                        kill_group(pgid, pgid, reap=self.service.reap_now)

                try:
                    try:
                        proc = self.service.spawn(work, cmd)
                    except Busy:
                        self._error(503, "busy")
                        return
                    pgid = proc.pid
                    self.service.register(name, pgid)

                    outputs = {"stdout": "", "stderr": ""}
                    byte_counts = {"stdout": 0, "stderr": 0}
                    exceeded = {"stdout": False, "stderr": False}
                    output_lock = threading.Lock()
                    truncation = threading.Event()

                    def stop_for_output():
                        truncation.set()
                        terminate()

                    def read_output(stream, pipe):
                        decoder = codecs.getincrementaldecoder("utf-8")("replace")
                        while True:
                            chunk = pipe.read(8192)
                            if not chunk:
                                break
                            with output_lock:
                                byte_counts[stream] += len(chunk)
                                text = decoder.decode(chunk)
                                combined = outputs[stream] + text
                                if len(combined) > MAX_OUTPUT:
                                    truncation.set()
                                    exceeded[stream] = True
                                    outputs[stream] = combined[:MAX_OUTPUT]
                                else:
                                    outputs[stream] = combined
                                too_much = byte_counts[stream] > 2 * 1024 * 1024
                                both_full = all(exceeded.values())
                            if too_much or both_full:
                                stop_for_output()
                        tail = decoder.decode(b"", final=True)
                        if tail:
                            with output_lock:
                                combined = outputs[stream] + tail
                                if len(combined) > MAX_OUTPUT:
                                    truncation.set()
                                outputs[stream] = combined[:MAX_OUTPUT]
                        pipe.close()

                    readers = [threading.Thread(target=read_output, args=(stream, pipe), daemon=True)
                               for stream, pipe in (("stdout", proc.stdout), ("stderr", proc.stderr))]
                    for reader in readers:
                        reader.start()

                    def watch_disk():
                        # The budget is measured while the command runs, so a command that keeps
                        # writing is stopped within a second instead of filling the disk. It only
                        # signals while the command is known to be running (see watch_lock).
                        while not finished.wait(DISK_POLL):
                            if self.service.over_budget(work):
                                with watch_lock:
                                    if not finished.is_set():
                                        terminate()
                                return

                    if max_mb() > 0:
                        disk_watcher = threading.Thread(target=watch_disk, daemon=True)
                        disk_watcher.start()
                    deadline = time.monotonic() + timeout
                    code, expired = self.service.wait_child(pgid, timeout=timeout, since=since)
                    if not expired:
                        # The shell is gone: from here on its id may be given to another command.
                        with watch_lock:
                            finished.set()
                    if expired:
                        timed_out = True
                        self.service.mark_exceeded(name, pgid)
                        terminate()
                        code, _ = self.service.wait_child(pgid, timeout=KILL_TIMEOUT, since=since)
                    # A shell may exit while a background descendant still holds
                    # the inherited output pipes open. The timeout bounds draining
                    # as well as waiting for the shell itself.
                    for reader in readers:
                        reader.join(max(0, deadline - time.monotonic()))
                    if any(reader.is_alive() for reader in readers):
                        timed_out = True
                        self.service.mark_exceeded(name, pgid)
                        terminate()
                        for reader in readers:
                            reader.join()
                    if code is None:
                        code = -1
                    answer = {"exit_code": code, "stdout": outputs["stdout"],
                              "stderr": outputs["stderr"], "timed_out": timed_out,
                              "truncated": truncation.is_set()}
                    note = self.service.full_note(work)
                    if note:
                        stderr = answer["stderr"]
                        if stderr and not stderr.endswith("\n"):
                            stderr += "\n"
                        answer["stderr"] = stderr + note
                    if debug_enabled():
                        answer["pgid"] = pgid
                    self._send(200, answer)
                finally:
                    finished.set()
                    if disk_watcher is not None:
                        # Before the command's ids are released: a late watcher must never signal an
                        # id that a new command has already been given.
                        disk_watcher.join()
                    with self.service.lock:
                        self.service.counts[name] -= 1
                        if not self.service.counts[name]: del self.service.counts[name]
                    if proc is not None:
                        if not timed_out:
                            # Do not leave the background children of the command behind.
                            terminate()
                        if code is None:
                            final, _ = self.service.wait_child(pgid, timeout=KILL_TIMEOUT, since=since)
                            if final is not None:
                                code = final
                        self.service.unregister(name, pgid)
                        # Whatever escaped the command's session goes now, not at the next sweep.
                        self.service.sweep_detached()
                        proc.returncode = code if code is not None else -1
            elif route == "/kill":
                with self.service.lock:
                    groups = list(self.service.running.get(name, set()))
                for pgid in groups:
                    kill_group(pgid, pgid, reap=self.service.reap_now)
                self.service.sweep_detached()
                self._send(200, {"killed": len(groups)})
            else:
                self._error(404, "not found")
        except ValueError as exc:
            self._error(400, exc)
        except Exception:
            self._error(500, "internal server error")


class SandboxServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_close(self):
        super().server_close()
        service = getattr(self, "service", None)
        if service is not None:
            service.stop()


def make_server(host, port, work_root):
    server = SandboxServer((host, port), Handler)
    server.service = Service(work_root)
    server.service.start()
    return server


def main():
    server = make_server("0.0.0.0", int(os.environ.get("PORT", "8100")),
                         os.environ.get("HUDDLE_WORK_ROOT", "/work"))
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

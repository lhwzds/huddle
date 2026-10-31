# Spec: the sandbox service (`sandbox/`)

Agents get a real shell. Anyone using a deployment may try to make an agent run hostile
commands, so the shell runs in its own container with no network, as a
non-root user, with CPU, memory, process and time limits. The backend reaches
it over an internal Docker network. The HTTP API is in `docs/specs/contracts.md`.

## Files

- `sandbox/server.py`: the service. Python 3.12 standard library only
  (`http.server.ThreadingHTTPServer`, `subprocess`, `json`, `os`, `signal`).
- `sandbox/Dockerfile`: `python:3.12-slim`, adds `bash coreutils procps`, creates user
  `agent` (uid 1000), `WORKDIR /work`, runs `python /app/server.py` as `agent`, port 8100.
- `sandbox/tests/test_server.py`: pytest tests that start the server on a free port
  in a thread with `HUDDLE_WORK_ROOT` set to a temp dir.

## Behaviour

- Work root: `HUDDLE_WORK_ROOT` (default `/work`). A workspace is `<root>/<workspace>`,
  created on first use. `workspace` must match `^[A-Za-z0-9_-]{1,64}$`, else 400.
- Paths: relative only; resolved with `os.path.realpath` and must stay inside the
  workspace (no `..`, no absolute paths, no symlink escape), else 400. Hidden
  entries (name starts with `.`) are skipped in listings.
- `POST /exec`: runs `["bash", "-c", cmd]` with `cwd` = the workspace,
  `start_new_session=True`, env reduced to `PATH`, `HOME=<workspace>`, `LANG=C.UTF-8`,
  `TERM=dumb`. `timeout_s` is clamped to 1–60. On timeout the whole process group gets
  SIGKILL and `timed_out` is true. stdout and stderr are each capped at 12 000
  characters (decoded UTF-8 with replacement); `truncated` is true if either was cut.
  Each process group is registered under its workspace while it runs.
- `POST /kill`: SIGKILL every registered process group of the workspace; returns how many.
- `GET /files`: recursive listing, at most 500 entries, sorted by path, `mtime` as a float.
- `GET /file`: up to 1 MB. Valid UTF-8 → `binary: false` and the text; otherwise
  `binary: true` and base64 content. Missing → 404.
- `PUT /file`: creates parent directories; content up to 1 MB as text; returns the size.
- Per workspace at most 4 concurrent `exec` calls; more → 429.
- JSON errors: `{"error": "..."}` with 400/404/429/500. Never a stack trace.
- Logs one line per request to stderr.

## Container limits (in `docker-compose.yml`, task `compose`)

`networks: [sandbox_net]` with `sandbox_net: {internal: true}`, `read_only: true`,
`tmpfs: /tmp`, volume `work:/work`, `user: "1000:1000"`, `cap_drop: [ALL]`,
`security_opt: [no-new-privileges:true]`, `pids_limit: 256`, `mem_limit: 1g`,
`cpus: 1.0`. The backend joins both the default network (to reach model APIs) and
`sandbox_net`.

## Tests (must pass with `cd sandbox && uv run --with pytest pytest -q`)

- exec echo; exit codes; stderr captured; `HOME` is the workspace; files persist
  between calls in one workspace and are invisible from another.
- timeout kills a `sleep 30` within ~2 s with `timed_out: true`, including a child
  process (`bash -c "sleep 30 & sleep 30"`).
- `/kill` stops a running `sleep 30` started by a concurrent `/exec`.
- output truncation at 12 000 characters.
- path traversal: `../x`, `/etc/passwd`, a symlink to `/etc` → 400; bad workspace → 400.
- write then read round trip; listing skips hidden files; binary file → base64.

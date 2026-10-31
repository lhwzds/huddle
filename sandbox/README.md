# Huddle sandbox

A standard-library HTTP service that runs agent shell commands and manages per-conversation files.
The API provides `POST /exec`, `POST /kill`, `GET /files`, `GET /file`, `PUT /file`, and `GET /health` (see `docs/specs/contracts.md`).
Run locally with `HUDDLE_WORK_ROOT=/tmp/huddle-work PORT=8100 python3 server.py`; run tests with `uv run --with pytest pytest -q` from this directory.

## Hostile commands

A command runs in its own session (`start_new_session=True`), so it can be
stopped as a whole. When a command runs into its timeout, produces too much
output or is stopped with `POST /kill`, its process group is signalled again
and again until the kernel reports that the group is really gone - a single
`SIGKILL` races with the processes that are still forking. On Linux the whole
session is swept as well, because a child can leave the process group of its
parent but never leaves the session.

The service also runs a reaper that collects the zombies of the orphaned
children of a command: the sandbox is pid 1 in the container, so they would
otherwise fill the process table. The container uses `init: true` (tini) for
the same reason.

When the process table is full and a command cannot be started, the groups of
the workspaces that already ran into their timeout are killed and the start is
retried once; if that fails as well the answer is `503 {"error": "busy"}`.

## Workspaces

A workspace is `<root>/<workspace>`, the working directory of one conversation.

- `HUDDLE_WORKSPACE_TTL_HOURS` (default 24, `0` switches the sweep off): a
  background thread deletes every workspace that was not touched for that long,
  every ten minutes. A workspace with a running command is never deleted.
- `HUDDLE_WORKSPACE_MAX_MB` (default 50): the disk budget of one workspace.
  `PUT /file` answers `413` when the workspace is over its budget, and a
  finished `/exec` writes `workspace is full (50 MB); delete files to continue`
  to its stderr.

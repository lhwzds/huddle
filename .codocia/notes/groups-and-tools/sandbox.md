---
title: The sandbox
order: 80
evidence:
  - sandbox/server.py:451-475 #7a8cddc8100d
  - sandbox/server.py:649-674 #ff5e0509431c
  - sandbox/server.py:312-342 #075df75d6bf9
  - sandbox/server.py:345-355 #e3cd3a9a3b3a
  - sandbox/server.py:729-741 #460af4ad5ee3
  - sandbox/server.py:379-408 #29551e65e544
  - docker-compose.yml:42-64 #10789983331a
  - sandbox/Dockerfile #db6402c43c48
  - backend/huddle/tools.py:70-71 #6dd7257d7d03
---

`bash`, `read_file` and `write_file` go over HTTP to a standard-library service in its own container: non-root, read-only root, no capabilities, CPU, memory and process limits, and no route to the internet. Each conversation has a workspace directory. A command runs with `bash -c` in a new session with a minimal environment, a clamped timeout, bounded output and at most four concurrent commands per workspace. Kills hit the whole process group until it is gone, so fork bombs are recoverable; `sweep_detached` (`HUDDLE_SANDBOX_SWEEP=1`, set in `sandbox/Dockerfile`) also kills any process that left its session with `setsid()`. Idle workspaces are deleted after a TTL. The disk budget counts hidden files and is checked about once a second while a command runs (best-effort). [SECURITY.md](../../SECURITY.md) states what this does not defend.

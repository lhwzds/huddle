---
title: Running it
order: 10
evidence:
  - docker-compose.yml #71bd60d03453
  - backend/huddle/main.py:28-87 #302f87601f65
  - docs/configuration.md:93-104 #88a1b365be4d
---

`docker compose up --build` starts three services: the static `frontend` on port 8080 (bound to `${HUDDLE_BIND:-127.0.0.1}`, so only this machine by default), the FastAPI `backend` with its SQLite file on a volume, and the `sandbox`. The sandbox sits only on an `internal` network: the backend can reach it, the internet cannot be reached from it. Setting `HUDDLE_MODEL=fake` runs a scripted model without any key.

`create_app` wires the store, the registry, the orchestrator and the sandbox client; at startup it refuses to run when the active model, or a model named by a stored member, has no key, marks messages left `streaming` by a previous process as stopped, and loads the groups.

To serve it to others, put any HTTPS reverse proxy in front of the frontend; `HUDDLE_BIND`, base path, unbuffered SSE and forwarded client addresses are covered in [Running behind a reverse proxy](../configuration.md#running-behind-a-reverse-proxy).

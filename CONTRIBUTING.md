# Contributing

Thanks for taking a look. Huddle is small on purpose: plain asyncio + httpx on the backend, React on the
frontend, a standard-library sandbox. Please keep it that way — no agent frameworks.

## Setup

```bash
cp .env.example .env          # add one model key, or set HUDDLE_MODEL=fake to run without one
docker compose up --build     # http://localhost:8080
```

For a faster loop without Docker (shell and file tools need the sandbox container, everything else works):
`cd backend && HUDDLE_DB=./dev.db uv run python -m huddle` and
`cd frontend && npm install && npm run dev` (Vite proxies `/api` to port 8000).

## Before you open a pull request

- `cd backend && uv run pytest -q`
- `uv run --project backend --with pytest pytest eval/tests -q`
- `cd sandbox && uv run --with pytest pytest -q`
- `cd frontend && npm test && npm run typecheck && npm run build`

Tests never touch the network: they use the fake model and a fake sandbox. A change in behaviour comes with a
test; a change in agent behaviour ideally also with an eval case in `eval/cases/` and its result on a real model.

## Where things go

- Model-facing text: `backend/huddle/prompts.py` only. It must not name preset agents or their facts.
- Scenario content (people, facts, example prompts): `config/groups/`.
- Intent words (stop, everyone, discussion, hand-off): `config/language.yaml`.
- Numbers: `limits:` in `config/huddle.yaml`. See [docs/configuration.md](docs/configuration.md).
- Provider quirks: `backend/huddle/llm/`.
- The shared contract (events, HTTP API, sandbox API) is `docs/specs/contracts.md`; change it together with
  both sides.

Nothing a user sees may reveal how steering works (no "redirecting", modes or internal logs).

## Security issues

Please don't open a public issue; see [SECURITY.md](SECURITY.md).

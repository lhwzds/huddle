# Huddle: instructions for coding agents

- Read `docs/architecture.md`, then the spec for the part you change in `docs/specs/`.
  `docs/specs/contracts.md` is the shared contract; do not change it unless your task says so.
- Backend: Python 3.12, uv project in `backend/`, package `huddle`. Run tests with
  `cd backend && uv run pytest -q`. No agent frameworks (no LangChain/LangGraph); plain asyncio + httpx.
- Frontend: `frontend/`, Vite + React + TypeScript; `npm test`, `npm run typecheck`, `npm run build`.
- Sandbox: `sandbox/`, standard library only; `cd sandbox && uv run --with pytest pytest -q`.
- Never put API keys in files. Keys come from environment variables named in `config/huddle.yaml`.
- All model-facing text lives in `backend/huddle/prompts.py`.
- Nothing the user sees may reveal the steering mechanism (no "redirecting", modes, logs).
- Tests must not touch the network; use the fake model and the fake sandbox.

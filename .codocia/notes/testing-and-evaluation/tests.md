---
title: Offline tests
order: 10
evidence:
  - backend/huddle/llm/fake.py:1-46 #9f032b7f38ac
  - backend/tests/conftest.py:13-38 #7a7ddd57047e
  - backend/tests/test_runtime.py:277-308 #9c347a6fcdaa
  - frontend/test/state.test.ts:44-58 #1fb20e81c54c
  - sandbox/tests/test_server.py:15-30 #7f8a006c955d
  - eval/tests/test_core.py:42-135 #1a6a601cf0f7
  - .github/workflows/ci.yml #c2efa3a2f299
---

Backend tests never touch the network. `FakeLLM` replies by rules keyed on agent, profile and prompt text, with configurable chunking and delays, so a test can make Theo stream slowly and send a steer mid-message. `FakeSandbox` records commands and can block one until released, which is how steering during a tool call is tested. Most runtime cases (steering, stop phrases, addressing, rounds, seams) live in `backend/tests/test_runtime.py`.

Frontend tests (Vitest) cover the event reducer, including gap detection, plus group, profile and follow-scroll helpers. Sandbox tests start the real service and run real processes. Eval tests run the checks, metrics and transcript markers on small synthetic event logs. CI runs all four suites on every push and pull request; [CONTRIBUTING.md](../../CONTRIBUTING.md) lists the same commands.

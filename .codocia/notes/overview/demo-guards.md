---
title: Guards for a public instance
order: 40
evidence:
  - backend/huddle/guards.py:1-5 #07b686f4a298
  - backend/huddle/guards.py:56-131 #8f445579fda4
  - frontend/nginx.conf:13 #837ded7e0d3b
  - backend/huddle/guards.py:195-280 #c391e7e8d333
  - backend/huddle/api.py:191-211 #70880496ee68
  - backend/huddle/metering.py:22-72 #9b8299ba3406
---

`guards.py` protects an instance that others can reach: an invite code exchanged for an access cookie that `AccessMiddleware` checks before any route runs, per-client rate limits, caps on messages per conversation and on busy conversations, and a daily token budget. Each guard is off unless its environment variable is set, so local runs and tests need none of them. Every model call goes through `MeteredLLM`, which records provider usage or an estimate, so the budget counts decide, dedupe and extraction calls too. Per-client limits key on `client_ip`, which counts `X-Forwarded-For` from the right: the frontend's nginx appends the address it saw and each of the `HUDDLE_TRUST_PROXY` proxies in front appends one more (`true`/`yes`/`on` means one), so entries a client wrote itself are never believed; `is_https` trusts `X-Forwarded-Proto` only behind trusted proxies. The variables are listed in [configuration](../configuration.md); what the sandbox does and does not defend is in [SECURITY.md](../../SECURITY.md).

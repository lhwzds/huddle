---
title: Web research
order: 70
evidence:
  - backend/huddle/web.py:1-9 #628138dd63a4
  - backend/huddle/web.py:140-207 #403431bce154
  - backend/huddle/web.py:456-507 #7decfbac1b19
  - backend/huddle/web.py:533-562 #80a7425d3468
  - backend/huddle/tools.py:116-129 #4c1b8c91cfba
---

Because the sandbox has no network, `web_search` and `fetch_url` run in the backend. Only http and https are allowed; redirects are followed by hand, and every hop's host is refused if any address is private, loopback, link-local, multicast or a cloud metadata name. `page_client` then connects only to an address that passed that check (`_PublicOnlyBackend` resolves the name once, at connection time), so DNS rebinding cannot reach a private address. Size, time and text length are capped, and errors reach the model as plain sentences. Each result is written into a research folder in the workspace with its source link, so the person can check what an agent read.

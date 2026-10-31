---
title: How the runner scores
order: 30
evidence:
  - eval/huddle_eval/runner.py:310-375 #d14392a5c49b
  - eval/huddle_eval/runner.py:46-53 #c361a51086a0
  - eval/huddle_eval/core.py:93-105 #ae99774f3516
  - eval/huddle_eval/core.py:142-160 #ae44b758a4e1
  - eval/huddle_eval/core.py:240-270 #3a6c5f0a47a3
  - eval/huddle_eval/core.py:296-317 #e0a9b19414fb
---

`eval/run.py` is black-box: it creates a conversation over HTTP, follows the SSE stream with receive timestamps, and plays the steps. "Idle" is the server's word, not a quiet period: the snapshot's `active` flag is false, nothing is typing or streaming, and the last send is at least 1.5 s old, for two polls in a row, so a slow decide call is not mistaken for the end. At the end it reloads and freezes the events at the snapshot's `last_seq`.

Deterministic checks are the hard gate: `same_message_continued` (a message has deltas stored both before and after the later user message, in the server's `seq` order, not by client clocks), `no_new_text_after_stop`, `reload_identical` (snapshot equals a replay of the event log), privacy `no_leak` phrases, `ui_words_absent` (no talk of steering or instructions), speaker sets, effort, files and latency. The judge is a configured model (`--judge`, else the active one) that reads a transcript with "user message arrives" markers inserted at the exact character offsets, and answers each statement with a verdict and reason.

---
title: Follow-up rounds and the end of a turn
order: 60
evidence:
  - backend/huddle/orchestrator.py:325-328 #3c13cb0cf8a9
  - backend/huddle/orchestrator.py:342-353 #73d28023cdb1
  - backend/huddle/orchestrator.py:367-380 #abf7e4e96e13
  - backend/huddle/orchestrator.py:462-469 #12ed8182ad01
  - config/language.yaml:48-56 #9e40ea940e09
  - config/huddle.yaml:61-64 #198ba8126936
---

After a round finishes, the next candidates depend on what was actually written. `_message_targets` reads only the finished text: an `@Name`, a name followed by a question in the same sentence, or a hand-off ("that's Theo's call"); the patterns are in `config/language.yaml`. The `addresses` field from `decide` is ignored, because models listed everyone there and every message became an invitation.

Outside a discussion or combine round, only addressed idle agents are candidates. The loop stops when nobody speaks, the round limit is reached, or the per-turn message cap is hit; `_turn_cap` grows the cap for large groups so everyone can speak once. That is why a turn ends on its own instead of turning into an endless exchange.

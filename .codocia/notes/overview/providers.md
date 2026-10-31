---
title: Provider quirks stay in adapters
order: 30
evidence:
  - backend/huddle/llm/openai_chat.py:15-41 #3c9bb2b7f34b
  - backend/huddle/llm/openai_chat.py:61-88 #975b20e46cc9
  - backend/huddle/llm/openai_chat.py:101-186 #9dce1079eb6a
  - config/huddle.yaml:14-23 #c9d115df8fa2
---

The runtime sees one stream of `TextDelta`, `ToolCallDone` and `Done` events. `OpenAIChat` hides the differences: `_ThinkFilter` drops `<think>` blocks, `_MarkupFilter` cuts native tool-call markup some models write as text (and reports the cut as `wanted_tools` on `Done`, see [[steering]]), and with `reasoning_passback` (set for `deepseek`) every assistant message carries its `reasoning_content`, which thinking models require once tools are involved. Transport errors, 429 and 5xx are retried only before the first event is yielded, so a retry can never duplicate visible text.

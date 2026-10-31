---
title: 提供方差异留在适配器里
source: #3ca8ca64b5d8
---

运行时只看到一种事件流：`TextDelta`、`ToolCallDone` 和 `Done`。`OpenAIChat` 屏蔽差异：`_ThinkFilter` 去掉 `<think>` 块，`_MarkupFilter` 截掉某些模型以文本形式写出的原生工具调用标记（并在 `Done` 上以 `wanted_tools` 报告这次截断，见 [[steering]]）；开启 `reasoning_passback` 后（`deepseek` 已开启），每条助手消息都带上自己的 `reasoning_content`，思考模型一旦涉及工具就要求如此。传输错误、429 和 5xx 只在产出第一个事件之前重试，因此重试绝不会重复可见文本。

---
title: 概览
---

# 概览

<sub>[English](../01-overview.md) · **中文**</sub>

Huddle 是一个网页群聊：一个人和一组 AI 智能体。每个智能体都有灵魂（它是谁）和私有记忆；几个智能体同时流式作答，彼此对话，只会重复别人时保持沉默，还能在写作途中被改变方向，而且仍是同一条消息。

运行时由两个对象承担：每个会话的 `Orchestrator` 决定谁发言（[一轮对话如何运作](02-how-it-works.md)），每条可见消息对应一个 `AgentRun`，负责写出它并吸收引导（[回答途中的引导](03-steering.md)）。[架构概览](../../architecture.md) 有图示和设计决策，[配置](../../configuration.md) 说明每一项设置，[SECURITY.md](../../../SECURITY.md) 说明沙箱防护什么；代码布局见 [代码结构](structure.md)。Huddle 采用 Apache-2.0 许可；[CONTRIBUTING.md](../../../CONTRIBUTING.md) 说明各类改动应放在哪里。

<sub>来源: [orchestrator.py:30-44](../../../backend/huddle/orchestrator.py#L30-L44) · [agent.py:65-97](../../../backend/huddle/agent.py#L65-L97) · [LICENSE:1-3](../../../LICENSE#L1-L3)</sub>

## 运行

`docker compose up --build` 启动三个服务：端口 8080 上的静态 `frontend`（绑定到 `${HUDDLE_BIND:-127.0.0.1}`，默认只有本机能访问）、带有 SQLite 文件（存放在卷上）的 FastAPI `backend`，以及 `sandbox`。沙箱只接入一个 `internal` 网络：后端能访问它，它却访问不到互联网。设置 `HUDDLE_MODEL=fake` 会运行一个脚本化模型，不需要任何密钥。

`create_app` 把存储、注册表、编排器和沙箱客户端连接起来；启动时，若当前模型或某个已存储成员指定的模型缺少密钥就拒绝运行，把上一个进程遗留的 `streaming` 消息标记为已停止，并加载各个群组。

要给别人使用，在前端前面放任意一个 HTTPS 反向代理；`HUDDLE_BIND`、基础路径、不缓冲的 SSE 和转发的客户端地址见[在反向代理后运行](../../configuration.md#running-behind-a-reverse-proxy)。

<sub>来源: [docker-compose.yml](../../../docker-compose.yml) · [main.py:28-87](../../../backend/huddle/main.py#L28-L87) · [configuration.md:93-104](../../../docs/configuration.md#L93-L104)</sub>

## 切换模型

模型由配置选择：`HUDDLE_MODEL` 选中 `config/huddle.yaml` 中 `models:` 下的一项（默认 `deepseek`），`make_llm` 构建对应的提供方（`openai_chat` 适用于任何 Chat Completions API，另有 `openai_responses` 和 `fake`）。代码里不写死任何模型。

每次调用都带一个 *profile*：`decide`、`steer`、`utility`，或努力等级 `light`、`normal`、`deep`。`params_for` 把该项的 `default` 参数与此 profile 的参数深度合并；正是靠这一个设置，引导调用用最低推理档，深度工作用更高档。智能体可以指定自己的模型；`Orchestrator._llm` 为每个模型键缓存一个客户端。各字段说明见[模型](../../configuration.md#models)。

<sub>来源: [huddle.yaml:1-46](../../../config/huddle.yaml#L1-L46) · [config.py:57-66](../../../backend/huddle/config.py#L57-L66) · [__init__.py](../../../backend/huddle/llm/__init__.py) · [orchestrator.py:132-136](../../../backend/huddle/orchestrator.py#L132-L136)</sub>

## 提供方差异留在适配器里

运行时只看到一种事件流：`TextDelta`、`ToolCallDone` 和 `Done`。`OpenAIChat` 屏蔽差异：`_ThinkFilter` 去掉 `<think>` 块，`_MarkupFilter` 截掉某些模型以文本形式写出的原生工具调用标记（并在 `Done` 上以 `wanted_tools` 报告这次截断，见 [回答途中的引导](03-steering.md)）；开启 `reasoning_passback` 后（`deepseek` 已开启），每条助手消息都带上自己的 `reasoning_content`，思考模型一旦涉及工具就要求如此。传输错误、429 和 5xx 只在产出第一个事件之前重试，因此重试绝不会重复可见文本。

<sub>来源: [openai_chat.py:15-41](../../../backend/huddle/llm/openai_chat.py#L15-L41) · [openai_chat.py:61-88](../../../backend/huddle/llm/openai_chat.py#L61-L88) · [openai_chat.py:101-186](../../../backend/huddle/llm/openai_chat.py#L101-L186) · [huddle.yaml:14-23](../../../config/huddle.yaml#L14-L23)</sub>

## 公开实例的防护

`guards.py` 保护别人也能访问的实例：用邀请码换取访问 cookie，由 `AccessMiddleware` 在任何路由运行前检查；按客户端的速率限制；每个会话的消息数和繁忙会话数上限；以及每日 token 预算。每项防护都要设置对应的环境变量才会开启，所以本地运行和测试都不需要它们。每次模型调用都经过 `MeteredLLM`，它记录提供方返回的用量或估算值，因此预算也计入 decide、去重和提取调用。按客户端的限制以 `client_ip` 为键，它从右往左数 `X-Forwarded-For`：前端的 nginx 追加它看到的地址，前面的 `HUDDLE_TRUST_PROXY` 个代理每个再追加一个（`true`/`yes`/`on` 表示一个），所以客户端自己写入的条目永远不被采信；`is_https` 只在受信任代理之后才相信 `X-Forwarded-Proto`。这些变量见[配置](../../configuration.md)；沙箱防护什么、不防护什么见 [SECURITY.md](../../../SECURITY.md)。

<sub>来源: [guards.py:1-5](../../../backend/huddle/guards.py#L1-L5) · [guards.py:56-131](../../../backend/huddle/guards.py#L56-L131) · [nginx.conf:13](../../../frontend/nginx.conf#L13) · [guards.py:195-280](../../../backend/huddle/guards.py#L195-L280) · [api.py:191-211](../../../backend/huddle/api.py#L191-L211) · +1</sub>

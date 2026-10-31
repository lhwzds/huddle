---
title: 公开实例的防护
source: #c628ea9e5eb6
---

`guards.py` 保护别人也能访问的实例：用邀请码换取访问 cookie，由 `AccessMiddleware` 在任何路由运行前检查；按客户端的速率限制；每个会话的消息数和繁忙会话数上限；以及每日 token 预算。每项防护都要设置对应的环境变量才会开启，所以本地运行和测试都不需要它们。每次模型调用都经过 `MeteredLLM`，它记录提供方返回的用量或估算值，因此预算也计入 decide、去重和提取调用。按客户端的限制以 `client_ip` 为键，它从右往左数 `X-Forwarded-For`：前端的 nginx 追加它看到的地址，前面的 `HUDDLE_TRUST_PROXY` 个代理每个再追加一个（`true`/`yes`/`on` 表示一个），所以客户端自己写入的条目永远不被采信；`is_https` 只在受信任代理之后才相信 `X-Forwarded-Proto`。这些变量见[配置](../../configuration.md)；沙箱防护什么、不防护什么见 [SECURITY.md](../../../SECURITY.md)。

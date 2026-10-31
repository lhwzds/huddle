---
title: 运行
source: #8f59ad4e390f
---

`docker compose up --build` 启动三个服务：端口 8080 上的静态 `frontend`（绑定到 `${HUDDLE_BIND:-127.0.0.1}`，默认只有本机能访问）、带有 SQLite 文件（存放在卷上）的 FastAPI `backend`，以及 `sandbox`。沙箱只接入一个 `internal` 网络：后端能访问它，它却访问不到互联网。设置 `HUDDLE_MODEL=fake` 会运行一个脚本化模型，不需要任何密钥。

`create_app` 把存储、注册表、编排器和沙箱客户端连接起来；启动时，若当前模型或某个已存储成员指定的模型缺少密钥就拒绝运行，把上一个进程遗留的 `streaming` 消息标记为已停止，并加载各个群组。

要给别人使用，在前端前面放任意一个 HTTPS 反向代理；`HUDDLE_BIND`、基础路径、不缓冲的 SSE 和转发的客户端地址见[在反向代理后运行](../../configuration.md#running-behind-a-reverse-proxy)。

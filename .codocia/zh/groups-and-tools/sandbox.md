---
title: 沙箱
source: #2ce70fc040e3
---

`bash`、`read_file` 和 `write_file` 通过 HTTP 发往一个只用标准库、运行在独立容器中的服务：非 root 用户、只读根文件系统、没有任何 capabilities、限制 CPU、内存和进程数，且无法连接互联网。每个会话有一个工作区目录。命令以 `bash -c` 在新会话中运行，环境最小化，超时被钳制，输出有上限，每个工作区最多同时运行四条命令。终止针对整个进程组，直到它彻底消失，所以 fork 炸弹可以恢复；`sweep_detached`（`HUDDLE_SANDBOX_SWEEP=1`，由 `sandbox/Dockerfile` 设置）还会杀掉用 `setsid()` 离开自身会话的进程。空闲工作区在 TTL 之后删除。磁盘预算计入隐藏文件，命令运行期间大约每秒检查一次（尽力而为）。[SECURITY.md](../../../SECURITY.md) 说明了它不防护什么。

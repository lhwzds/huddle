# Security

## Reporting

Please report vulnerabilities privately through GitHub's "Report a vulnerability" (Security tab) rather than
in a public issue. Include steps to reproduce. You should get an answer within a few days.

## What Huddle defends

Agents run shell commands and fetch web pages on behalf of whoever is chatting, so treat every message as
untrusted input.

- **Shell.** `bash` and file tools run in the `sandbox` container: no network (internal Docker network only),
  non-root user, read-only root filesystem, all capabilities dropped, `no-new-privileges`, and CPU, memory,
  process-count and per-command time limits. A command's whole session is killed on timeout or stop, and any
  process that leaves its session (`setsid`) is killed within seconds. A workspace's size, hidden files
  included, is measured every second while a command runs, and a command that goes over the budget is
  stopped; this is a best-effort budget, not a quota (see Known limits).
  Idle workspaces expire (`HUDDLE_WORKSPACE_TTL_HOURS`, `HUDDLE_WORKSPACE_MAX_MB`). The file API is confined to
  the conversation's workspace.
- **Web.** Both tools run in the backend. `web_search` only posts to the search endpoint in `config/huddle.yaml`
  (trusted configuration; it may be a private service). `fetch_url` reads any page the agent names: only http(s); every hop of every redirect is resolved
  and refused if it points at a private, loopback, link-local or otherwise non-public address, and the connection
  goes to exactly the address that was checked (a name that re-resolves elsewhere is never contacted). Proxy
  settings from the environment are ignored. Response sizes and the number of web calls per message are capped.
- **Keys.** Model keys come from environment variables or a token file named in `config/huddle.yaml`; they are
  never sent to the browser, the sandbox or the model.
- **Cost and abuse.** For a public instance set an invite code (`HUDDLE_ACCESS_CODE`), per-client rate limits and
  a daily token budget (see [`.env.example`](.env.example)). They are off by default. Per-client means per
  address: set `HUDDLE_TRUST_PROXY` to the number of proxies in front, so the address cannot be spoofed with
  `X-Forwarded-For` and the invite cookie is `Secure` behind HTTPS. This holds only if the app's port is
  reachable by nothing but that proxy; `docker-compose.yml` binds it to `127.0.0.1` unless `HUDDLE_BIND` says
  otherwise.

## Known limits

- The sandbox is a container, not a VM. For a hostile public audience, run it on a host you are willing to lose,
  or under gVisor/Kata.
- Agents follow instructions from the conversation; prompt injection through fetched pages can change what an
  agent says or runs inside the sandbox. It cannot reach the network from the shell or read the backend's
  secrets.
- There are no user accounts. Anyone with the invite code shares the same instance: they can open every
  conversation, its files and its groups.
- All conversations share one sandbox container and one user inside it. A shell command can therefore read and
  change other conversations' workspaces (which, without accounts, everyone can already see). Per-conversation
  isolation would need a container per conversation.
- The disk budget measures the files in a workspace once a second. A fast writer exceeds it briefly, and a
  hostile command can avoid it (an unlinked file kept open, or writing into another workspace). For a hard
  limit, put the `work` volume on its own size-limited filesystem.

# Huddle

Think a question through with a group of AI agents. Huddle is an open-source group chat where
each agent has its own role, voice and private knowledge the others cannot see. They answer at
the same time, talk to each other, combine what they know, stay quiet when they would only repeat
someone, and can be redirected mid-sentence: the answer they are writing changes right there.

Three groups ship as presets (a startup team of 3, a family of 5, a launch war room of 10), and
any group can be edited or extended in the app.

## Quick start

```sh
cp .env.example .env          # set the key for the model you use
docker compose up --build     # frontend, backend + SQLite, sandbox
```

Open <http://localhost:8080>. No key? Set `HUDDLE_MODEL=fake` in `.env` to click through with a
scripted model. Switching models is configuration only (`HUDDLE_MODEL`, see
[`config/huddle.yaml`](config/huddle.yaml)).

## Try this

In the startup group:

- "Should we take the Acme deal?" — no single agent knows enough to answer alone.
- While they write: "Theo, one sentence max." — only Theo changes, in the same message.
- "From now on, answer in Chinese" — applies right away and after a reload.
- "Let's discuss: should we finish Kestrel before we sign Acme?" — a short discussion and a conclusion.
- "Theo, write a rollout plan as plan.md" — the file appears in the Files panel.

## Documentation

- [Architecture](docs/architecture.md): diagrams, the turn flow, steering, design decisions
- [Configuration](docs/configuration.md): models, limits, groups, language, running behind a proxy
- [Security](SECURITY.md): what the sandbox and the web tools defend, and known limits
- [The book](docs/book/README.md): a guided tour of the code ([中文](docs/book/zh/README.md))
- [Specs](docs/specs/): contracts, runtime, sandbox, frontend, testing, evaluation

## Development

Tests run without network or keys: `cd backend && uv run pytest -q`, `cd frontend && npm test`,
`cd sandbox && uv run --with pytest pytest -q`. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[Apache License 2.0](LICENSE)

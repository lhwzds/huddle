#!/usr/bin/env bash
set -euo pipefail

port="${HUDDLE_PORT:-8080}"
base="http://localhost:${port}"
cleanup() {
  docker compose down
}
trap cleanup EXIT

docker compose up -d --build
for _ in $(seq 1 90); do
  if curl --fail --silent "$base/api/health" >/dev/null; then
    break
  fi
  sleep 2
done
curl --fail --silent "$base/api/health" >/dev/null

conversation=$(curl --fail --silent -X POST "$base/api/conversations" \
  -H 'Content-Type: application/json' -d '{}')
id=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' <<<"$conversation")
curl --fail --silent -X POST "$base/api/conversations/$id/messages" \
  -H 'Content-Type: application/json' -d '{"text":"hi"}' >/dev/null

for _ in $(seq 1 90); do
  snapshot=$(curl --fail --silent "$base/api/conversations/$id")
  if python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if any(m.get("author") not in ("user", "system") for m in d["messages"]) else 1)' <<<"$snapshot"; then
    echo "OK: agent replied in conversation $id"
    exit 0
  fi
  sleep 2
done
echo "Timed out waiting for agent response" >&2
exit 1

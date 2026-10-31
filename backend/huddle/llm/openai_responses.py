"""OpenAI Responses API streaming provider.

Mirrors `openai_chat` (same retries, timeouts and cancellation behaviour) but
speaks the Responses API: `POST {base_url}/responses` with `instructions`,
`input` items and flat function tools.
"""
import asyncio
import json
import os
import re
from pathlib import Path

import httpx

from ..config import params_for
from .base import Done, TextDelta, ToolCall, ToolCallDone, Usage

DEFAULT_TIMEOUT = httpx.Timeout(connect=10, read=60, write=30, pool=10)

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ApiKeyError(RuntimeError, ValueError):
    """The API key is missing or empty. Names the source, never the value."""


class ResponseFailed(RuntimeError):
    """The model reported a failed response (`response.failed` / `error`)."""


def _expand(value: str) -> str:
    """Expand `${VAR}` / `${VAR:-default}` in a header value."""
    def replace(match: re.Match) -> str:
        var, default = match.groups()
        found = os.environ.get(var)
        if not found:
            return default if default is not None else ""
        return found
    return _ENV_PATTERN.sub(replace, value)


def _usage(raw) -> Usage | None:
    if not raw:
        return None
    return Usage(int(raw.get("input_tokens") or 0), int(raw.get("output_tokens") or 0))


def _error_message(data: dict) -> str:
    payload = data.get("response") or {}
    error = payload.get("error") or data.get("error")
    if isinstance(error, dict):
        message = error.get("message") or ""
    elif error:
        message = str(error)
    else:
        message = ""
    message = message or data.get("message") or payload.get("status") or ""
    return message or "response failed"


async def _lines(response, read_timeout):
    # Custom transports (including MockTransport) do not enforce httpx's read
    # timeout. Bound each SSE read here too, and close the response on timeout.
    iterator = response.aiter_lines()
    while True:
        try:
            async with asyncio.timeout(read_timeout):
                line = await anext(iterator)
        except StopAsyncIteration:
            return
        except TimeoutError as exc:
            raise httpx.ReadTimeout("Model stream stalled", request=response.request) from exc
        yield line


class OpenAIResponses:
    """Streams from the OpenAI Responses API."""

    def __init__(self, model_cfg: dict, client: httpx.AsyncClient | None = None, *, timeout: httpx.Timeout | None = None):
        self.cfg = model_cfg
        self.client = client
        self.timeout = timeout or (client.timeout if client is not None else DEFAULT_TIMEOUT)

    # -- auth -------------------------------------------------------------
    def resolve_key(self) -> str:
        """Return the API key, read fresh on every request (the token rotates).

        The key comes from `api_key_file` (a path, `~` expanded) or from
        `api_key_env`. A missing or empty source raises an error naming the
        source, never the value.
        """
        env = self.cfg.get("api_key_env")
        file = self.cfg.get("api_key_file")
        file_error = None
        if file:
            path = Path(str(file)).expanduser()
            try:
                value = path.read_text(encoding="utf-8").strip()
            except OSError:
                value = ""
            if value:
                return value
            file_error = f"Missing API key file: {path}"
        if env:
            value = (os.environ.get(env) or "").strip()
            if value:
                return value
            env_error = f"Missing API key environment variable: {env}"
            raise ApiKeyError(f"{file_error}; {env_error}" if file_error else env_error)
        if file_error:
            raise ApiKeyError(file_error)
        return ""

    def build_headers(self, key: str) -> dict:
        headers = {}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        for name, value in (self.cfg.get("headers") or {}).items():
            headers[str(name)] = _expand(str(value))
        return headers

    # -- request ----------------------------------------------------------
    def build_body(self, messages, *, profile: str, max_tokens: int, tools=None) -> dict:
        system: list[str] = []
        items: list[dict] = []
        for message in messages:
            role = message.role
            if role == "system":
                system.append(message.content)
            elif role == "user":
                items.append({"role": "user", "content": message.content})
            elif role == "assistant":
                if message.content:
                    items.append({"role": "assistant", "content": message.content})
                for call in message.tool_calls or []:
                    items.append({"type": "function_call", "call_id": call.id, "name": call.name, "arguments": call.arguments})
            elif role == "tool":
                items.append({"type": "function_call_output", "call_id": message.tool_call_id or "", "output": message.content})
        body = {
            "model": self.cfg["model"],
            "instructions": "\n\n".join(system),
            "input": items,
            "stream": True,
            "store": False,
            "max_output_tokens": max_tokens,
        }
        if tools:
            body["tools"] = [{"type": "function", "name": t.name, "description": t.description, "parameters": t.parameters} for t in tools]
        body.update(params_for(self.cfg, profile))
        return body

    # -- stream -----------------------------------------------------------
    async def stream(self, messages, *, profile, max_tokens, tools=None):
        body = self.build_body(messages, profile=profile, max_tokens=max_tokens, tools=tools)
        url = str(self.cfg["base_url"]).rstrip("/") + "/responses"
        client = self.client or httpx.AsyncClient(timeout=self.timeout)
        own_client = self.client is None
        yielded = False
        try:
            for attempt in range(3):
                try:
                    # The key is read on every request: the token rotates.
                    headers = self.build_headers(self.resolve_key())
                    async with client.stream("POST", url, json=body, headers=headers, timeout=self.timeout) as response:
                        response.raise_for_status()
                        calls: list[ToolCall] = []
                        usage = None
                        event_name = ""
                        async for line in _lines(response, getattr(self.timeout, "read", None)):
                            if not line or line.startswith(":"):
                                continue
                            if line.startswith("event:"):
                                event_name = line[len("event:"):].strip()
                                continue
                            if not line.startswith("data:"):
                                continue
                            payload = line[len("data:"):].strip()
                            if not payload or payload == "[DONE]":
                                continue
                            try:
                                data = json.loads(payload)
                            except json.JSONDecodeError:
                                continue
                            if not isinstance(data, dict):
                                continue
                            kind = data.get("type") or event_name
                            if kind == "response.output_text.delta":
                                text = data.get("delta") or ""
                                if text:
                                    yielded = True
                                    yield TextDelta(text)
                            elif kind == "response.output_item.done":
                                item = data.get("item") or {}
                                if item.get("type") == "function_call":
                                    calls.append(ToolCall(id=item.get("call_id") or "", name=item.get("name") or "", arguments=item.get("arguments") or ""))
                            elif kind == "response.completed":
                                payload_response = data.get("response") or {}
                                usage = _usage(payload_response.get("usage") or data.get("usage"))
                                break
                            elif kind in ("response.failed", "response.incomplete", "error"):
                                raise ResponseFailed(_error_message(data))
                        for call in calls:
                            yielded = True
                            yield ToolCallDone(call)
                        yielded = True
                        yield Done("tool_calls" if calls else "stop", usage)
                        return
                except (httpx.TransportError, httpx.HTTPStatusError, ResponseFailed) as exc:
                    retryable = (isinstance(exc, (httpx.TransportError, ResponseFailed))
                                 or exc.response.status_code == 429 or exc.response.status_code >= 500)
                    if yielded or not retryable or attempt == 2:
                        raise
                    await asyncio.sleep((0.5, 1.5)[attempt])
        finally:
            if own_client:
                await client.aclose()


OpenAIResponsesLLM = OpenAIResponses
OpenAIResponsesProvider = OpenAIResponses

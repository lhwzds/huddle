"""OpenAI-compatible streaming chat completion provider."""
import asyncio
import json
import logging
import os
from dataclasses import asdict

import httpx
from ..config import params_for
from .base import Done, TextDelta, ToolCall, ToolCallDone, Usage

log = logging.getLogger(__name__)


class _ThinkFilter:
    def __init__(self):
        self.inside = False
        self.pending = ""

    def feed(self, text, *, final=False):
        self.pending += text
        out = []
        while self.pending:
            tag = "</think>" if self.inside else "<think>"
            pos = self.pending.find(tag)
            if pos >= 0:
                if not self.inside:
                    out.append(self.pending[:pos])
                self.pending = self.pending[pos + len(tag):]
                self.inside = not self.inside
                continue
            retain = 0
            if not final:
                for n in range(1, min(len(tag) - 1, len(self.pending)) + 1):
                    if self.pending.endswith(tag[:n]):
                        retain = n
            if not self.inside:
                out.append(self.pending[:-retain] if retain else self.pending)
            self.pending = self.pending[-retain:] if retain else ""
            break
        return "".join(out)

DEFAULT_TIMEOUT = httpx.Timeout(connect=10, read=60, write=30, pool=10)


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


class _MarkupFilter:
    """Drops a model's native tool-call markup (e.g. DeepSeek's "<｜｜DSML｜｜ calls>") written as text.

    Everything from the first marker on is suppressed; a possible partial marker at a chunk end is held back.
    """
    MARKERS = ("<｜DSML｜", "<｜｜DSML｜｜", "<|DSML|")

    def __init__(self):
        self.pending = ""
        self.dropping = False

    def feed(self, text, *, final=False):
        if self.dropping:
            return ""
        self.pending += text
        hits = [i for i in (self.pending.find(m) for m in self.MARKERS) if i >= 0]
        if hits:
            out, self.pending, self.dropping = self.pending[:min(hits)].rstrip(), "", True
            return out
        if final:
            out, self.pending = self.pending, ""
            return out
        cut = self.pending.rfind("<")
        if cut >= 0 and len(self.pending) - cut < 12:
            out, self.pending = self.pending[:cut], self.pending[cut:]
            return out
        out, self.pending = self.pending, ""
        return out


class OpenAIChat:
    def __init__(self, model_cfg: dict, client: httpx.AsyncClient | None = None, *, timeout: httpx.Timeout | None = None):
        self.cfg = model_cfg
        key_env = model_cfg.get("api_key_env")
        if key_env and not os.environ.get(key_env):
            raise ValueError(f"Missing API key environment variable: {key_env}")
        self.key = os.environ[key_env] if key_env else ""
        self.client = client
        self.timeout = timeout or (client.timeout if client is not None else DEFAULT_TIMEOUT)

    async def stream(self, messages, *, profile, max_tokens, tools=None):
        body = {"model": self.cfg["model"], "messages": [], "stream": True, "max_tokens": max_tokens}
        for message in messages:
            item = {"role": message.role, "content": message.content}
            if message.tool_calls is not None:
                item["tool_calls"] = [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}} for c in message.tool_calls]
            if message.role == "assistant" and self.cfg.get("reasoning_passback"):
                # Thinking models (e.g. DeepSeek) reject any assistant message without its reasoning
                # once tools are involved, even an empty one; send it on every assistant message.
                # Other providers never get the field.
                item["reasoning_content"] = message.reasoning or ""
            if message.tool_call_id is not None:
                item["tool_call_id"] = message.tool_call_id
            body["messages"].append(item)
        if tools:
            body["tools"] = [{"type": "function", "function": asdict(t)} for t in tools]
        if self.cfg.get("include_usage"):
            body["stream_options"] = {"include_usage": True}
        body.update(params_for(self.cfg, profile))
        url = self.cfg["base_url"].rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self.key}"}
        client = self.client or httpx.AsyncClient(timeout=self.timeout)
        own_client = self.client is None
        yielded = False
        try:
            for attempt in range(3):
                try:
                    async with client.stream("POST", url, json=body, headers=headers, timeout=self.timeout) as response:
                        if response.status_code >= 400:
                            # Keep the provider's explanation; it is the only clue to a rejected request.
                            detail = (await response.aread()).decode("utf-8", "replace")[:500]
                            log.warning("model API %s: %s", response.status_code, detail)
                        response.raise_for_status()
                        think = _ThinkFilter()
                        markup = _MarkupFilter()
                        calls = {}
                        reasoning = []
                        finish_reason = "stop"
                        usage = None
                        async for line in _lines(response, self.timeout.read):
                            if not line.startswith("data:"):
                                continue
                            payload = line[5:].strip()
                            if not payload:
                                continue
                            if payload == "[DONE]":
                                break
                            item = json.loads(payload)
                            if item.get("usage"):
                                u = item["usage"]
                                usage = Usage(u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
                            for choice in item.get("choices", []):
                                if choice.get("finish_reason"):
                                    finish_reason = choice["finish_reason"]
                                delta = choice.get("delta") or {}
                                if delta.get("reasoning_content"):
                                    reasoning.append(delta["reasoning_content"])
                                if delta.get("content"):
                                    text = markup.feed(think.feed(delta["content"]))
                                    if text:
                                        yielded = True
                                        yield TextDelta(text)
                                for part in delta.get("tool_calls") or []:
                                    call = calls.setdefault(part["index"], {"id": "", "name": "", "arguments": ""})
                                    call["id"] += part.get("id") or ""
                                    function = part.get("function") or {}
                                    call["name"] += function.get("name") or ""
                                    call["arguments"] += function.get("arguments") or ""
                        text = markup.feed(think.feed("", final=True), final=True)
                        if text:
                            yielded = True
                            yield TextDelta(text)
                        for index in sorted(calls):
                            yielded = True
                            yield ToolCallDone(ToolCall(**calls[index]))
                        yielded = True
                        yield Done(finish_reason, usage, "".join(reasoning), wanted_tools=markup.dropping)
                        return
                except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                    retryable = isinstance(exc, httpx.TransportError) or exc.response.status_code == 429 or exc.response.status_code >= 500
                    if yielded or not retryable or attempt == 2:
                        raise
                    await asyncio.sleep((0.5, 1.5)[attempt])
        finally:
            if own_client:
                await client.aclose()

OpenAIChatLLM = OpenAIChat

"""Token accounting for the daily token budget."""
from __future__ import annotations

import logging

from .context import estimate_tokens
from .llm.base import Done, TextDelta

log = logging.getLogger(__name__)


def estimate_input(messages) -> int:
    """The token estimate of the context that is sent to the model."""
    total = 0
    for message in messages:
        total += estimate_tokens(message.content)
        for call in message.tool_calls or []:
            total += estimate_tokens(call.name + call.arguments)
    return total


class TokenMeter:
    """Counts the tokens of every model call in the usage table."""

    def __init__(self, store):
        self.store = store
        self.tokens = 0

    async def record(self, tokens: int):
        if not tokens or tokens <= 0:
            return
        self.tokens += tokens
        from .guards import daily_token_budget, utc_day
        if daily_token_budget() is None:
            return  # The budget is off: nothing is counted.
        try:
            await self.store.add_usage(utc_day(), tokens)
        except Exception:
            log.warning("Could not record the token usage", exc_info=True)


class MeteredLLM:
    """A model that reports how many tokens every call used."""

    def __init__(self, inner, meter: TokenMeter):
        self.inner = inner
        self.meter = meter

    async def stream(self, messages, *, profile, max_tokens, tools=None):
        inner = self.inner.stream(messages, profile=profile, max_tokens=max_tokens, tools=tools)
        output: list[str] = []
        usage = None
        try:
            async for event in inner:
                if isinstance(event, TextDelta):
                    output.append(event.text)
                elif isinstance(event, Done):
                    usage = event.usage
                yield event
        finally:
            # The wrapped stream must be closed here: the caller only knows
            # about this generator.
            try:
                await inner.aclose()
            except Exception:
                pass
            await self.meter.record(self._tokens(messages, output, usage))

    def _tokens(self, messages, output, usage) -> int:
        if usage is not None:
            return max(0, int(usage.input_tokens or 0) + int(usage.output_tokens or 0))
        return estimate_input(messages) + estimate_tokens("".join(output))

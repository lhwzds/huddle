"""The token accounting that the daily budget is built on."""
from math import ceil

from huddle.guards import utc_day
from huddle.llm.base import ChatMessage, Done, TextDelta, Usage
from huddle.metering import MeteredLLM, TokenMeter, estimate_input


class UsageLLM:
    """A model that reports how many tokens it used."""

    def __init__(self, usage):
        self.usage = usage

    async def stream(self, messages, *, profile, max_tokens, tools=None):
        yield TextDelta('hello')
        yield Done('stop', self.usage)


class PlainLLM:
    """A model that reports nothing."""

    async def stream(self, messages, *, profile, max_tokens, tools=None):
        yield TextDelta('hello')
        yield Done('stop', None)


async def drain(llm, messages):
    async for _ in llm.stream(messages, profile='normal', max_tokens=10):
        pass


async def test_the_usage_of_the_provider_is_counted(store):
    meter = TokenMeter(store)
    llm = MeteredLLM(UsageLLM(Usage(100, 20)), meter)
    await drain(llm, [ChatMessage('user', 'hi')])
    assert meter.tokens == 120


async def test_the_estimate_is_counted_without_a_usage(store):
    meter = TokenMeter(store)
    llm = MeteredLLM(PlainLLM(), meter)
    messages = [ChatMessage('system', 'x' * 40), ChatMessage('user', 'y' * 8)]
    await drain(llm, messages)
    assert meter.tokens == estimate_input(messages) + ceil(len('hello') / 4)


async def test_the_tokens_are_stored_per_day(store, monkeypatch):
    monkeypatch.setenv('HUDDLE_DAILY_TOKEN_BUDGET', '1000')
    meter = TokenMeter(store)
    llm = MeteredLLM(UsageLLM(Usage(100, 20)), meter)
    await drain(llm, [ChatMessage('user', 'hi')])
    assert await store.usage_tokens(utc_day()) == 120
    await drain(llm, [ChatMessage('user', 'hi')])
    assert await store.usage_tokens(utc_day()) == 240


async def test_nothing_is_stored_without_a_budget(store, monkeypatch):
    monkeypatch.delenv('HUDDLE_DAILY_TOKEN_BUDGET', raising=False)
    meter = TokenMeter(store)
    llm = MeteredLLM(UsageLLM(Usage(100, 20)), meter)
    await drain(llm, [ChatMessage('user', 'hi')])
    assert await store.usage_tokens(utc_day()) == 0

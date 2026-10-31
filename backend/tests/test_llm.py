import json
import httpx
import pytest
from huddle.llm import ChatMessage, Done, TextDelta, ToolCall, ToolCallDone, ToolSpec, Usage, complete_json
from huddle.llm.fake import FakeLLM, FakeReply, rule, when
from huddle.llm.openai_chat import OpenAIChat

def sse(*items):
    return ''.join('data: ' + json.dumps(item) + '\n\n' for item in items) + 'data: [DONE]\n\n'

def chunk(delta=None, finish=None, usage=None):
    return {'choices': [{'delta': delta or {}, 'finish_reason': finish}] if delta is not None or finish else [], 'usage': usage}

def cfg():
    return {'base_url': 'https://test/v1', 'model': 'test', 'api_key_env': 'TEST_LLM_KEY', 'include_usage': True, 'params': {'default': {'nested': {'a': 1, 'b': 2}}, 'deep': {'nested': {'b': 3}}}}

async def test_stream_content_thinking_tools_usage_params(monkeypatch):
    monkeypatch.setenv('TEST_LLM_KEY', 'private')
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, text=sse(
            chunk({'content': 'Hi <thi', 'reasoning_content': 'secret'}),
            chunk({'content': 'nk>hidden</th'}),
            chunk({'content': 'ink> there'}),
            chunk({'tool_calls': [{'index': 1, 'id': 'b', 'function': {'name': 'read', 'arguments': '{"p"'}}]}),
            chunk({'tool_calls': [{'index': 0, 'id': 'a', 'function': {'name': 'bash', 'arguments': '{"cmd":'}}, {'index': 1, 'function': {'arguments': ':1}'}}]}),
            chunk({'tool_calls': [{'index': 0, 'function': {'arguments': '"ls"}'}}]}, 'tool_calls'),
            chunk(usage={'prompt_tokens': 12, 'completion_tokens': 4})), headers={'content-type': 'text/event-stream'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        llm = OpenAIChat(cfg(), client)
        events = [e async for e in llm.stream([ChatMessage('user', 'hi')], profile='deep', max_tokens=99, tools=[ToolSpec('bash', 'run', {'type': 'object'})])]
    assert ''.join(e.text for e in events if isinstance(e, TextDelta)) == 'Hi  there'
    assert [e.call for e in events if isinstance(e, ToolCallDone)] == [ToolCall('a', 'bash', '{"cmd":"ls"}'), ToolCall('b', 'read', '{"p":1}')]
    # Reasoning never becomes visible text, but is returned so it can be passed back after tool calls.
    assert events[-1] == Done('tool_calls', Usage(12, 4), 'secret')
    body = json.loads(requests[0].content)
    assert body['nested'] == {'a': 1, 'b': 3}
    assert body['tools'][0]['function']['name'] == 'bash'
    assert body['stream_options'] == {'include_usage': True}
    assert str(requests[0].url) == 'https://test/v1/chat/completions'

async def test_retry_only_before_first_event(monkeypatch):
    monkeypatch.setenv('TEST_LLM_KEY', 'private')
    calls = 0
    async def no_sleep(_): pass
    monkeypatch.setattr('huddle.llm.openai_chat.asyncio.sleep', no_sleep)
    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(200, text=sse(chunk({'content': 'hello'})))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        llm = OpenAIChat(cfg(), client)
        events = [e async for e in llm.stream([], profile='light', max_tokens=1)]
    assert calls == 2 and events[0] == TextDelta('hello')

    class FailingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'
            raise httpx.ReadError('broken')
    calls = 0
    def failing(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, stream=FailingStream())
    async with httpx.AsyncClient(transport=httpx.MockTransport(failing)) as client:
        llm = OpenAIChat(cfg(), client)
        with pytest.raises(httpx.ReadError):
            _ = [e async for e in llm.stream([], profile='light', max_tokens=1)]
    assert calls == 1

async def test_aclose_closes_response(monkeypatch):
    monkeypatch.setenv('TEST_LLM_KEY', 'private')
    class Stream(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
            yield b'data: [DONE]\n\n'
        async def aclose(self):
            self.closed = True
    stream = Stream()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=stream))) as client:
        gen = OpenAIChat(cfg(), client).stream([], profile='light', max_tokens=1)
        assert await anext(gen) == TextDelta('hello')
        await gen.aclose()
        assert stream.closed

async def test_complete_json_fake_and_cancel():
    fake = FakeLLM([rule(when(agent='mira', profile='decide', contains='hi'), FakeReply('before ```json\n{"ok": true}\n``` after', chunk=100, delay=0))])
    msgs = [ChatMessage('system', 'You are Mira, CFO. Your agent key is mira.'), ChatMessage('user', 'hi')]
    assert await complete_json(fake, msgs, profile='decide', max_tokens=10) == {'ok': True}
    assert fake.calls[0].agent == 'mira' and fake.calls[0].last_user == 'hi'
    gen = FakeLLM([], FakeReply('hello', chunk=1, delay=0))
    stream = gen.stream([], profile='light', max_tokens=10)
    await anext(stream)
    await stream.aclose()
    assert gen.cancelled == 1
    with pytest.raises(ValueError):
        await complete_json(FakeLLM([], FakeReply('no json', delay=0)), [], profile='utility', max_tokens=10)

async def test_demo_and_hook():
    from huddle.llm import make_llm
    import asyncio
    llm = make_llm({'models': {'fake': {'provider': 'fake'}}}, 'fake')
    msgs = [ChatMessage('system', 'You are Theo, CTO. Your agent key is theo.'), ChatMessage('user', 'Hi')]
    assert (await complete_json(llm, msgs, profile='decide', max_tokens=30))['speak'] is True
    assert await complete_json(llm, msgs, profile='utility', max_tokens=30) == {'drop': [], 'instructions': []}
    assert ''.join([e.text async for e in llm.stream(msgs, profile='light', max_tokens=30) if isinstance(e, TextDelta)]) == 'Theo here.'
    entered, release = asyncio.Event(), asyncio.Event()
    async def hook():
        entered.set()
        await release.wait()
    fake = FakeLLM([], FakeReply('abc', chunk=3, delay=0, before_done=hook))
    gen = fake.stream(msgs, profile='light', max_tokens=3)
    assert await anext(gen) == TextDelta('abc')
    pending = asyncio.create_task(anext(gen))
    await entered.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert fake.cancelled == 1


async def test_stalled_stream_times_out_and_closes_without_retry(monkeypatch):
    import asyncio
    from huddle.llm.openai_chat import DEFAULT_TIMEOUT
    monkeypatch.setenv('TEST_LLM_KEY', 'private')
    assert DEFAULT_TIMEOUT == httpx.Timeout(connect=10, read=60, write=30, pool=10)

    class Stalled(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
            await asyncio.Event().wait()
        async def aclose(self):
            self.closed = True

    body = Stalled()
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        llm = OpenAIChat(cfg(), client, timeout=httpx.Timeout(.01))
        stream = llm.stream([], profile='normal', max_tokens=20)
        assert await anext(stream) == TextDelta('first')
        async with asyncio.timeout(1):  # watchdog, not the timeout being tested
            with pytest.raises(httpx.ReadTimeout, match='stalled'):
                await anext(stream)
    assert body.closed
    assert len(requests) == 1  # no retry once any visible output has been received


async def test_reasoning_is_passed_back_on_assistant_tool_calls(monkeypatch):
    monkeypatch.setenv('TEST_LLM_KEY', 'private')
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, text=sse(chunk({'content': 'ok'}, 'stop')), headers={'content-type': 'text/event-stream'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        llm = OpenAIChat(cfg() | {'reasoning_passback': True}, client)
        messages = [ChatMessage('user', 'list files'),
                    ChatMessage('assistant', tool_calls=[ToolCall('c1', 'bash', '{"cmd":"ls"}')], reasoning='I should list.'),
                    ChatMessage('tool', 'plan.md', tool_call_id='c1'),
                    ChatMessage('assistant', 'done')]
        [e async for e in llm.stream(messages, profile='deep', max_tokens=50)]
    sent = json.loads(requests[0].content)['messages']
    assert sent[1]['reasoning_content'] == 'I should list.'
    assert sent[3]['reasoning_content'] == '' and 'reasoning_content' not in sent[0] and 'reasoning_content' not in sent[2]


async def test_reasoning_field_only_for_passback_models(monkeypatch):
    monkeypatch.setenv('TEST_LLM_KEY', 'private')
    bodies = []
    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, text=sse(chunk({'content': 'ok'}, 'stop')), headers={'content-type': 'text/event-stream'})
    call = ChatMessage('assistant', tool_calls=[ToolCall('c1', 'bash', '{}')], reasoning=None)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        for extra in ({}, {'reasoning_passback': True}):
            llm = OpenAIChat(cfg() | extra, client)
            [e async for e in llm.stream([ChatMessage('user', 'x'), call, ChatMessage('tool', 'y', tool_call_id='c1')], profile='deep', max_tokens=5)]
    assert 'reasoning_content' not in bodies[0]['messages'][1]
    assert bodies[1]['messages'][1]['reasoning_content'] == ''



async def test_native_tool_markup_never_reaches_the_text(monkeypatch):
    monkeypatch.setenv('TEST_LLM_KEY', 'private')
    def handler(request):
        return httpx.Response(200, text=sse(
            chunk({'content': 'Here is the summary.'}),
            chunk({'content': '\n\n<｜｜DS'}),
            chunk({'content': 'ML｜｜ calls>\n<｜｜DSML｜｜ invoke name="write_file">'}, 'stop')),
            headers={'content-type': 'text/event-stream'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        llm = OpenAIChat(cfg(), client)
        events = [e async for e in llm.stream([ChatMessage('user', 'hi')], profile='deep', max_tokens=50)]
    assert ''.join(e.text for e in events if isinstance(e, TextDelta)).rstrip() == 'Here is the summary.'

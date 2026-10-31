import json
from pathlib import Path

import httpx
import pytest

from huddle.llm import ChatMessage, Done, TextDelta, ToolCall, ToolCallDone, ToolSpec, Usage, make_llm
from huddle.llm.openai_responses import OpenAIResponses


def sse(*events):
    """Canned SSE: each event is (name, data)."""
    parts = []
    for name, data in events:
        parts.append(f'event: {name}\n')
        parts.append('data: ' + json.dumps(data) + '\n\n')
    return ''.join(parts)


def text_event(text):
    return ('response.output_text.delta', {'type': 'response.output_text.delta', 'delta': text})


def call_event(call_id, name, arguments):
    return ('response.output_item.done', {'type': 'response.output_item.done', 'item': {
        'type': 'function_call', 'call_id': call_id, 'name': name, 'arguments': arguments}})


def reasoning_event():
    return ('response.output_item.done', {'type': 'response.output_item.done', 'item': {
        'type': 'reasoning', 'id': 'rs_1', 'summary': []}})


def completed_event(input_tokens=12, output_tokens=4):
    return ('response.completed', {'type': 'response.completed', 'response': {
        'id': 'resp_1', 'status': 'completed',
        'usage': {'input_tokens': input_tokens, 'output_tokens': output_tokens}}})


def cfg(tmp_path, **extra):
    base = {'base_url': 'https://test/v1', 'model': 'test-model',
            'api_key_file': str(tmp_path / 'token'),
            'params': {'default': {'reasoning': {'effort': 'low'}},
                       'deep': {'reasoning': {'effort': 'medium'}}}}
    base.update(extra)
    return base


async def test_text_deltas_usage_and_request_body(tmp_path):
    (tmp_path / 'token').write_text('secret-token\n')
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text=sse(
            text_event('Hello'),
            text_event(' there'),
            completed_event(12, 4)), headers={'content-type': 'text/event-stream'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        llm = OpenAIResponses(cfg(tmp_path), client)
        events = [e async for e in llm.stream([ChatMessage('system', 'You are Mira'),
                                               ChatMessage('user', 'hi')],
                                              profile='deep', max_tokens=99)]
    assert ''.join(e.text for e in events if isinstance(e, TextDelta)) == 'Hello there'
    assert events[-1] == Done('stop', Usage(12, 4))
    body = json.loads(requests[0].content)
    assert body['model'] == 'test-model'
    assert body['instructions'] == 'You are Mira'
    assert body['input'] == [{'role': 'user', 'content': 'hi'}]
    assert body['stream'] is True
    assert body['store'] is False
    assert body['max_output_tokens'] == 99
    assert body['reasoning'] == {'effort': 'medium'}
    assert str(requests[0].url) == 'https://test/v1/responses'
    assert requests[0].headers['authorization'] == 'Bearer secret-token'


async def test_function_call_item_yields_tool_call_done(tmp_path):
    (tmp_path / 'token').write_text('secret-token')

    def handler(request):
        return httpx.Response(200, text=sse(
            reasoning_event(),
            text_event('let me look'),
            call_event('call_1', 'bash', '{"cmd": "ls"}'),
            completed_event(20, 7)), headers={'content-type': 'text/event-stream'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        llm = OpenAIResponses(cfg(tmp_path), client)
        events = [e async for e in llm.stream([ChatMessage('user', 'list files')], profile='light', max_tokens=50)]
    calls = [e.call for e in events if isinstance(e, ToolCallDone)]
    assert calls == [ToolCall('call_1', 'bash', '{"cmd": "ls"}')]
    assert events[-1] == Done('tool_calls', Usage(20, 7))


async def test_tool_round_trip_input_items(tmp_path):
    (tmp_path / 'token').write_text('secret-token')
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text=sse(completed_event()), headers={'content-type': 'text/event-stream'})

    messages = [
        ChatMessage('system', 'You are Mira'),
        ChatMessage('user', 'hi'),
        ChatMessage('assistant', 'working on it', tool_calls=[ToolCall('call_1', 'bash', '{"cmd": "ls"}')]),
        ChatMessage('tool', 'file.txt', tool_call_id='call_1'),
    ]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        llm = OpenAIResponses(cfg(tmp_path), client)
        _ = [e async for e in llm.stream(messages, profile='light', max_tokens=10,
                                         tools=[ToolSpec('bash', 'run a command', {'type': 'object'})])]
    body = json.loads(requests[0].content)
    assert body['instructions'] == 'You are Mira'
    assert body['input'] == [
        {'role': 'user', 'content': 'hi'},
        {'role': 'assistant', 'content': 'working on it'},
        {'type': 'function_call', 'call_id': 'call_1', 'name': 'bash', 'arguments': '{"cmd": "ls"}'},
        {'type': 'function_call_output', 'call_id': 'call_1', 'output': 'file.txt'},
    ]
    assert body['tools'] == [{'type': 'function', 'name': 'bash', 'description': 'run a command',
                               'parameters': {'type': 'object'}}]


async def test_multiple_system_messages_are_joined(tmp_path):
    (tmp_path / 'token').write_text('secret-token')
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text=sse(completed_event()), headers={'content-type': 'text/event-stream'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        llm = OpenAIResponses(cfg(tmp_path), client)
        _ = [e async for e in llm.stream([ChatMessage('system', 'one'), ChatMessage('system', 'two'),
                                          ChatMessage('user', 'hi')], profile='light', max_tokens=10)]
    body = json.loads(requests[0].content)
    assert 'one' in body['instructions'] and 'two' in body['instructions']


async def test_token_is_read_from_the_file_on_every_request(tmp_path):
    token_file = tmp_path / 'token'
    token_file.write_text('first-token\n')
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text=sse(text_event('ok'), completed_event()),
                              headers={'content-type': 'text/event-stream'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        llm = OpenAIResponses(cfg(tmp_path), client)
        _ = [e async for e in llm.stream([ChatMessage('user', 'hi')], profile='light', max_tokens=10)]
        token_file.write_text('  second-token  \n')
        _ = [e async for e in llm.stream([ChatMessage('user', 'hi again')], profile='light', max_tokens=10)]
    assert len(requests) == 2
    assert requests[0].headers['authorization'] == 'Bearer first-token'
    assert requests[1].headers['authorization'] == 'Bearer second-token'


async def test_missing_key_names_the_source(tmp_path):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as client:
        llm = OpenAIResponses(cfg(tmp_path, api_key_file=str(tmp_path / 'nope')), client)
        with pytest.raises(Exception) as exc:
            _ = [e async for e in llm.stream([ChatMessage('user', 'hi')], profile='light', max_tokens=10)]
    assert 'nope' in str(exc.value)


async def test_retry_on_503_before_the_first_event_only(tmp_path, monkeypatch):
    (tmp_path / 'token').write_text('secret-token')
    calls = 0

    async def no_sleep(_):
        return None

    monkeypatch.setattr('huddle.llm.openai_responses.asyncio.sleep', no_sleep)

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(200, text=sse(text_event('hello'), completed_event()),
                              headers={'content-type': 'text/event-stream'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        llm = OpenAIResponses(cfg(tmp_path), client)
        events = [e async for e in llm.stream([ChatMessage('user', 'hi')], profile='light', max_tokens=10)]
    assert calls == 2
    assert events[0] == TextDelta('hello')

    class FailingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'event: response.output_text.delta\ndata: {"type": "response.output_text.delta", "delta": "x"}\n\n'
            raise httpx.ReadError('broken')

    calls = 0

    def failing(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, stream=FailingStream())

    async with httpx.AsyncClient(transport=httpx.MockTransport(failing)) as client:
        llm = OpenAIResponses(cfg(tmp_path), client)
        with pytest.raises(httpx.ReadError):
            _ = [e async for e in llm.stream([ChatMessage('user', 'hi')], profile='light', max_tokens=10)]
    assert calls == 1


async def test_response_failed_raises(tmp_path, monkeypatch):
    (tmp_path / 'token').write_text('secret-token')

    async def no_sleep(_):
        return None

    monkeypatch.setattr('huddle.llm.openai_responses.asyncio.sleep', no_sleep)

    def handler(request):
        return httpx.Response(200, text=sse(
            ('response.failed', {'type': 'response.failed', 'response': {'error': {'message': 'boom'}}})),
            headers={'content-type': 'text/event-stream'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        llm = OpenAIResponses(cfg(tmp_path), client)
        with pytest.raises(RuntimeError, match='boom'):
            _ = [e async for e in llm.stream([ChatMessage('user', 'hi')], profile='light', max_tokens=10)]


async def test_error_event_raises(tmp_path):
    (tmp_path / 'token').write_text('secret-token')

    def handler(request):
        return httpx.Response(200, text=sse(
            ('error', {'type': 'error', 'code': 'server_error', 'message': 'upstream exploded'})),
            headers={'content-type': 'text/event-stream'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        llm = OpenAIResponses(cfg(tmp_path), client)
        with pytest.raises(RuntimeError, match='upstream exploded'):
            _ = [e async for e in llm.stream([ChatMessage('user', 'hi')], profile='light', max_tokens=10)]


async def test_aclose_closes_the_response(tmp_path):
    (tmp_path / 'token').write_text('secret-token')

    class Stream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b'event: response.output_text.delta\ndata: {"type": "response.output_text.delta", "delta": "hello"}\n\n'

        async def aclose(self):
            self.closed = True

    stream = Stream()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=stream))) as client:
        gen = OpenAIResponses(cfg(tmp_path), client).stream([ChatMessage('user', 'hi')], profile='light', max_tokens=10)
        assert await anext(gen) == TextDelta('hello')
        await gen.aclose()
    assert stream.closed


async def test_extra_headers_are_sent(tmp_path, monkeypatch):
    (tmp_path / 'token').write_text('secret-token')
    monkeypatch.setenv('HUDDLE_TEST_HEADER', 'from-env')
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text=sse(completed_event()), headers={'content-type': 'text/event-stream'})

    config = cfg(tmp_path, headers={'X-Trace': '${HUDDLE_TEST_HEADER}', 'X-Static': 'plain'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        llm = OpenAIResponses(config, client)
        _ = [e async for e in llm.stream([ChatMessage('user', 'hi')], profile='light', max_tokens=10)]
    assert requests[0].headers['x-trace'] == 'from-env'
    assert requests[0].headers['x-static'] == 'plain'


def test_make_llm_registers_the_provider(tmp_path):
    config = {'models': {'plan': {'provider': 'openai_responses', 'base_url': 'https://test/v1',
                                  'model': 'test-model', 'api_key_file': str(tmp_path / 'token')}}}
    llm = make_llm(config, 'plan')
    assert isinstance(llm, OpenAIResponses)


class _Sandbox:
    async def exec(self, workspace, cmd, timeout_s):
        return {'exit_code': 0, 'stdout': '', 'stderr': '', 'timed_out': False, 'truncated': False}

    async def kill(self, workspace):
        return 1

    async def files(self, workspace):
        return []

    async def read(self, workspace, path):
        return {'path': path, 'content': '', 'binary': False}

    async def write(self, workspace, path, content):
        return {'path': path, 'size': len(content)}


async def test_startup_key_check_accepts_an_api_key_file(tmp_path):
    from huddle.main import create_app

    token = tmp_path / 'token'
    token.write_text('secret-token\n')
    from copy import deepcopy
    from huddle.config import load_config
    cfg = deepcopy(load_config())
    cfg.update({'model': 'plan', 'database': str(tmp_path / 'test.db'), 'sandbox_url': 'http://sandbox:8100'})
    cfg['models'] = {'plan': {'provider': 'openai_responses', 'base_url': 'https://test/v1',
                              'model': 'test-model', 'api_key_file': str(token)}}
    app = create_app(cfg, llm_factory=lambda c, k: None, sandbox=_Sandbox())
    async with app.router.lifespan_context(app):
        pass

    empty = tmp_path / 'empty-token'
    empty.write_text('   \n')
    cfg['models']['plan']['api_key_file'] = str(empty)
    app = create_app(cfg, llm_factory=lambda c, k: None, sandbox=_Sandbox())
    with pytest.raises(RuntimeError) as exc:
        async with app.router.lifespan_context(app):
            pass
    assert str(empty) in str(exc.value)

    cfg['models']['plan']['api_key_file'] = str(tmp_path / 'missing-token')
    app = create_app(cfg, llm_factory=lambda c, k: None, sandbox=_Sandbox())
    with pytest.raises(RuntimeError) as exc:
        async with app.router.lifespan_context(app):
            pass
    assert 'missing-token' in str(exc.value)


async def test_key_from_environment_variable(tmp_path, monkeypatch):
    monkeypatch.setenv('TEST_RESPONSES_KEY', 'env-token')
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text=sse(completed_event()), headers={'content-type': 'text/event-stream'})

    config = cfg(tmp_path, api_key_env='TEST_RESPONSES_KEY')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        llm = OpenAIResponses(config, client)
        _ = [e async for e in llm.stream([ChatMessage('user', 'hi')], profile='light', max_tokens=10)]
    assert requests[0].headers['authorization'] == 'Bearer env-token'


async def test_missing_environment_variable_names_the_variable(tmp_path, monkeypatch):
    monkeypatch.delenv('TEST_RESPONSES_KEY', raising=False)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as client:
        llm = OpenAIResponses(cfg(tmp_path, api_key_env='TEST_RESPONSES_KEY'), client)
        with pytest.raises(Exception) as exc:
            _ = [e async for e in llm.stream([ChatMessage('user', 'hi')], profile='light', max_tokens=10)]
    assert 'TEST_RESPONSES_KEY' in str(exc.value)


async def test_tilde_in_the_key_file_is_expanded(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'token').write_text('home-token\n')
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setattr(Path, 'home', lambda: home)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text=sse(completed_event()), headers={'content-type': 'text/event-stream'})

    config = cfg(tmp_path, api_key_file='~/token')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        llm = OpenAIResponses(config, client)
        _ = [e async for e in llm.stream([ChatMessage('user', 'hi')], profile='light', max_tokens=10)]
    assert requests[0].headers['authorization'] == 'Bearer home-token'

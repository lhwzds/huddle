"""Exercise the HTTP/SSE boundary with an in-memory transport, never a network."""
import asyncio
import json
from pathlib import Path
import sys
import time

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from huddle_eval.core import idle_poll
from huddle_eval.prompts import JUDGE_SYSTEM, judge_user
from huddle_eval.runner import judge_statement, run_case


class FakeSSE(httpx.AsyncByteStream):
    def __init__(self, state, cursor):
        self.state, self.cursor = state, cursor

    async def __aiter__(self):
        while True:
            for e in self.state['events'][self.cursor:]:
                self.cursor += 1
                yield ('id: %s\nevent: %s\ndata: %s\n\n' % (e['seq'], e['type'], json.dumps(e))).encode()
            await asyncio.sleep(.005)


@pytest.mark.asyncio
async def test_case_http_sse():
    state = {'messages': [], 'events': []}
    def add(typ, data):
        state['events'].append({'seq': len(state['events'])+1, 'type': typ, 'data': data, 'ts': 'server time'})
    def message(id, author, text, status):
        return {'id': id, 'author': author, 'turn': 1, 'reply_to': [], 'pos': len(state['events'])+1, 'text': text, 'status': status, 'effort': 'light' if author != 'user' else None}
    def handle(request):
        path = request.url.path
        if request.method == 'POST' and path == '/api/conversations':
            return httpx.Response(201, json={'id': 'c'})
        if path.endswith('/events'):
            return httpx.Response(200, stream=FakeSSE(state, int(request.url.params.get('after', '0'))))
        if path.endswith('/messages'):
            user = message('u', 'user', 'hello', 'done'); add('message.created', {'message': user})
            agent = message('a', 'mira', '', 'streaming'); add('message.created', {'message': agent.copy()})
            add('message.delta', {'id': 'a', 'text': 'hi'}); add('message.done', {'id': 'a', 'status': 'done'})
            agent.update(text='hi', status='done'); state['messages'] = [user, agent]
            return httpx.Response(202, json={'message': user})
        if path == '/api/conversations/c':
            return httpx.Response(200, json={'last_seq': len(state['events']), 'messages': state['messages'], 'agents': [{'key': 'mira', 'name': 'Mira', 'role': 'CFO', 'typing': False}], 'active': False})
        return httpx.Response(404)
    case = {'id': 'test', 'category': 'test', 'steps': [{'say': 'hello'}, {'wait': {'ms': 20}}, {'reload': True}], 'checks': [{'spoke': {'turn': 1, 'include': ['mira']}}, {'reload_identical': True}]}
    async with httpx.AsyncClient(base_url='http://example.test', transport=httpx.MockTransport(handle)) as client:
        result = await run_case(client, case, 1, None)
    assert result['error'] is None
    assert all(s['pass'] for s in result['steps'])
    assert all(c['pass'] for c in result['checks'])
    assert len(result['events']) == 4
    assert all('received_at_utc' in e for e in result['events'])
    assert result['sends'][0]['sent_at_utc']


def snapshot(active=False, typing=False, streaming=False):
    return {'active': active,
            'agents': [{'key': 'mira', 'name': 'Mira', 'role': 'CFO', 'typing': typing}],
            'messages': [{'author': 'mira', 'status': 'streaming' if streaming else 'done', 'text': ''}]}


def test_idle_needs_two_quiet_polls_and_no_active_work():
    streak = 0
    # The server still has work in flight.
    settled, streak = idle_poll(snapshot(active=True), 0, 10, streak)
    assert not settled and streak == 0
    # An agent typing or streaming keeps the conversation busy.
    settled, streak = idle_poll(snapshot(typing=True), 0, 10, streak)
    assert not settled and streak == 0
    settled, streak = idle_poll(snapshot(streaming=True), 0, 10, streak)
    assert not settled and streak == 0
    # One quiet poll is not enough; two in a row settle the conversation.
    settled, streak = idle_poll(snapshot(), 0, 10, streak)
    assert not settled and streak == 1
    settled, streak = idle_poll(snapshot(), 0, 10, streak)
    assert settled and streak == 2


def test_idle_waits_for_the_last_user_message():
    streak = 0
    settled, streak = idle_poll(snapshot(), 10, 11.0, streak)
    assert not settled and streak == 0
    settled, streak = idle_poll(snapshot(), 10, 11.5, streak)
    assert not settled and streak == 1
    settled, streak = idle_poll(snapshot(), 10, 11.6, streak)
    assert settled and streak == 2
    # A new message resets the streak.
    settled, streak = idle_poll(snapshot(active=True), 11.6, 20, streak)
    assert not settled and streak == 0


@pytest.mark.asyncio
async def test_wait_idle_polls_the_snapshot_until_two_inactive_polls():
    from huddle_eval.runner import Session
    snapshots = [snapshot(active=True), snapshot(), snapshot()]
    calls = []

    def handler(request):
        if request.url.path == '/api/conversations/c':
            calls.append(1)
            return httpx.Response(200, json=dict(snapshots[min(len(calls) - 1, len(snapshots) - 1)], last_seq=0))
        return httpx.Response(404)

    async with httpx.AsyncClient(base_url='http://example.test', transport=httpx.MockTransport(handler)) as client:
        session = Session(client, 'c')
        sent = time.monotonic() - 10
        session.sends = [{'kind': 'say', 'time': sent, 'returned_at': sent}]
        await session.wait('idle')
    assert len(calls) == 3


def test_judge_prompt_states_the_annotation_rule():
    assert '⟪' in JUDGE_SYSTEM and '⟫' in JUDGE_SYSTEM
    assert 'annotation added by the evaluator' in JUDGE_SYSTEM
    assert 'not part of any message' in JUDGE_SYSTEM
    assert 'only in its own words' in JUDGE_SYSTEM
    assert 'every relevant message satisfies it' in JUDGE_SYSTEM
    assert 'the condition does not occur' in JUDGE_SYSTEM
    assert 'Judge only what the statement claims' in JUDGE_SYSTEM
    assert 'Transcript:' in judge_user('the transcript', 'a statement')
    assert 'a statement' in judge_user('the transcript', 'a statement')


@pytest.mark.asyncio
async def test_judge_call_uses_the_deep_profile_and_a_token_budget():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"verdict": true, "reason": "yes"}'}}]})

    cfg = {'model': 'judge-model', 'base_url': 'http://judge.test/v1',
           'params': {'default': {'reasoning_effort': 'minimal'}, 'deep': {'reasoning_effort': 'low'}}}
    async with httpx.AsyncClient(base_url='http://judge.test', transport=httpx.MockTransport(handler)) as client:
        verdict = await judge_statement(client, (cfg, 'secret'), 'Every reply is in Chinese.', 'the transcript')
    assert verdict['verdict'] is True and verdict['reason'] == 'yes'
    assert seen['max_tokens'] == 1500
    assert seen['reasoning_effort'] == 'low'
    assert seen['messages'][0]['content'] == JUDGE_SYSTEM
    assert 'the transcript' in seen['messages'][1]['content']


@pytest.mark.asyncio
async def test_stream_reconnects_after_drop_without_duplicate_events():
    from huddle_eval.runner import Session
    state = {'events': [{'seq': i, 'type': 'x', 'data': {}, 'ts': 't'} for i in range(1, 4)], 'dropped': False, 'params': []}

    class DroppingSSE(httpx.AsyncByteStream):
        def __init__(self, cursor): self.cursor = cursor
        async def __aiter__(self):
            events = state['events'][self.cursor:]
            for event in events:
                if not state['dropped'] and event['seq'] == 1:
                    state['dropped'] = True
                    yield ('data: %s\n\n' % json.dumps(event)).encode()
                    raise httpx.ReadError('connection dropped')
                yield ('data: %s\n\n' % json.dumps(event)).encode()
                await asyncio.sleep(.001)

    def handler(request):
        cursor = int(request.url.params['after']); state['params'].append(cursor)
        return httpx.Response(200, stream=DroppingSSE(cursor))

    async with httpx.AsyncClient(base_url='http://example.test', transport=httpx.MockTransport(handler)) as client:
        session = Session(client, 'c')
        task = asyncio.create_task(session.stream()); session.stream_task = task
        await asyncio.wait_for(session.ready.wait(), 1)
        for _ in range(100):
            if len(session.events) == 3: break
            await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert [e['seq'] for e in session.events] == [1, 2, 3]
        assert session.reconnect_count >= 1
        assert state['params'][:2] == [0, 1]


@pytest.mark.asyncio
async def test_wait_idle_errors_when_stream_task_is_dead():
    from huddle_eval.runner import Session
    async with httpx.AsyncClient(base_url='http://example.test', transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))) as client:
        session = Session(client, 'c')
        async def ended(): return
        session.stream_task = asyncio.create_task(ended())
        await session.stream_task
        with pytest.raises(RuntimeError, match='SSE stream failed'):
            await session.wait('idle')

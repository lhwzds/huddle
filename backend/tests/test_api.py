import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from huddle.api import cookie_path, events
from huddle.main import create_app
from conftest import FakeSandbox

async def test_http_api(app_client):
    client = app_client
    assert (await client.get('/api/health')).json() == {'ok': True, 'model': 'fake'}
    assert [a['key'] for a in (await client.get('/api/groups/startup/agents')).json()] == ['mira', 'theo', 'juno']
    created = await client.post('/api/conversations', json={'title': 'hello'})
    assert created.status_code == 201
    conv = created.json()
    cid = conv['id']
    assert (await client.get('/api/conversations')).json() == [conv]
    assert (await client.post(f'/api/conversations/{cid}/messages', json={'text': '   '})).status_code == 422
    assert (await client.post(f'/api/conversations/{cid}/messages', json={'text': 'x' * 8001})).status_code == 422
    posted = await client.post(f'/api/conversations/{cid}/messages', json={'text': ' hi '})
    assert posted.status_code == 202
    msg = posted.json()['message']
    assert msg['text'] == 'hi' and msg['turn'] == 1 and msg['status'] == 'done'
    snap = (await client.get(f'/api/conversations/{cid}')).json()
    assert snap['conversation'] == conv and snap['messages'] == [msg] and snap['last_seq'] == 1
    assert snap['agents'][0]['typing'] is False
    assert (await client.post(f'/api/conversations/{cid}/stop')).status_code == 202
    assert (await client.get(f'/api/conversations/{cid}/files')).json() == []
    assert (await client.get(f'/api/conversations/{cid}/files/no')).status_code == 404
    for path, method in [('', 'get'), ('/events', 'get'), ('/files', 'get'), ('/files/a', 'get'), ('/stop', 'post')]:
        assert (await getattr(client, method)(f'/api/conversations/unknown{path}')).status_code == 404
    assert (await client.post('/api/conversations/unknown/messages', json={'text': 'x'})).status_code == 404

async def get_frame(iterator):
    frame = await asyncio.wait_for(anext(iterator), 2)
    return json.loads(next(line[6:] for line in frame.splitlines() if line.startswith('data: ')))

async def test_sse_replay_header_and_live_race(cfg, store):
    app = create_app(cfg, store=store, sandbox=FakeSandbox())
    request = SimpleNamespace(app=app)
    cid = (await store.create_conversation())['id']
    for i in range(4):
        await store.add_message(cid, author='user', turn=i + 1, status='done', text=str(i))
    response = await events(request, cid, after=2, last_event_id='1')
    iterator = response.body_iterator
    assert (await get_frame(iterator))['seq'] == 3
    assert (await get_frame(iterator))['seq'] == 4
    await store.add_message(cid, author='user', turn=5, status='done')
    assert (await get_frame(iterator))['seq'] == 5
    await iterator.aclose()
    response = await events(request, cid, after=None, last_event_id='3')
    iterator = response.body_iterator
    assert [(await get_frame(iterator))['seq'] for _ in range(2)] == [4, 5]
    await iterator.aclose()
    # Subscribed but not yet replayed: all events appear exactly once.
    response = await events(request, cid, after=0, last_event_id=None)
    iterator = response.body_iterator
    for i in range(20):
        await store.add_message(cid, author='user', turn=i+6, status='done')
    assert [(await get_frame(iterator))['seq'] for _ in range(25)] == list(range(1, 26))
    pending = asyncio.create_task(anext(iterator))
    await asyncio.sleep(0)
    await store.add_message(cid, author='user', turn=26, status='done')
    frame = await asyncio.wait_for(pending, 2)
    assert json.loads(next(line[6:] for line in frame.splitlines() if line.startswith('data: ')))['seq'] == 26
    await iterator.aclose()
    assert not store.bus._subscribers

async def test_missing_key_startup(cfg, monkeypatch):
    cfg['model'] = 'minimax'
    monkeypatch.delenv('MINIMAX_API_KEY', raising=False)
    app = create_app(cfg, sandbox=FakeSandbox())
    with pytest.raises(RuntimeError, match='MINIMAX_API_KEY'):
        async with app.router.lifespan_context(app):
            pass

async def test_sse_actual_asgi_stream_overlap(cfg, store, monkeypatch):
    app = create_app(cfg, store=store, sandbox=FakeSandbox())
    cid = (await store.create_conversation())['id']
    for i in range(3):
        await store.add_message(cid, author='user', turn=i+1, status='done', text='start')
    # Pause after the replay query while the subscription is already active.
    replay_read = asyncio.Event()
    continue_replay = asyncio.Event()
    original = store.events_after
    async def delayed_replay(conv_id, after):
        replay = await original(conv_id, after)
        replay_read.set()
        await continue_replay.wait()
        return replay
    monkeypatch.setattr(store, 'events_after', delayed_replay)
    outbound = asyncio.Queue()
    disconnect = asyncio.Event()
    first_request = True
    async def receive():
        nonlocal first_request
        if first_request:
            first_request = False
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        await disconnect.wait()
        return {'type': 'http.disconnect'}
    async def send(message):
        await outbound.put(message)
    scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.3'},
             'http_version': '1.1', 'method': 'GET', 'scheme': 'http',
             'path': f'/api/conversations/{cid}/events', 'raw_path': f'/api/conversations/{cid}/events'.encode(),
             'query_string': b'after=1', 'headers': [(b'last-event-id', b'0')],
             'server': ('test', 80), 'client': ('test', 1), 'root_path': ''}
    task = asyncio.create_task(app(scope, receive, send))
    try:
        start = await asyncio.wait_for(outbound.get(), 2)
        assert start['status'] == 200
        headers = dict(start['headers'])
        assert headers[b'content-type'].startswith(b'text/event-stream')
        assert headers[b'cache-control'] == b'no-cache'
        assert headers[b'x-accel-buffering'] == b'no'
        await asyncio.wait_for(replay_read.wait(), 2)
        for i in range(20):
            await store.add_message(cid, author='user', turn=i+4, status='done')
        continue_replay.set()
        seen = []
        for _ in range(22):
            body = (await asyncio.wait_for(outbound.get(), 2))['body'].decode()
            lines = body.splitlines()
            assert lines[0].startswith('id: ') and lines[1] == 'event: message.created'
            data = json.loads(lines[2][6:])
            assert lines[0] == f"id: {data['seq']}"
            seen.append(data['seq'])
        assert seen == list(range(2, 24))
    finally:
        continue_replay.set()
        disconnect.set()
        await asyncio.wait_for(task, 2)
    assert not store.bus._subscribers

async def test_app_startup_recovers_streaming(cfg, tmp_path):
    from huddle.store import Store
    store = Store(cfg['database'])
    await store.open()
    cid = (await store.create_conversation())['id']
    await store.add_message(cid, author='mira', turn=1)
    await store.close()
    app = create_app(cfg, store=store, sandbox=FakeSandbox())
    async with app.router.lifespan_context(app):
        assert (await store.messages(cid))[0]['status'] == 'stopped'
        assert len(await store.events_after(cid, 0)) == 1

async def test_files_proxy_and_sandbox_client(cfg):
    import httpx
    from huddle.tools import Sandbox
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path == '/exec':
            return httpx.Response(200, json={'exit_code': 0})
        if request.url.path == '/kill':
            return httpx.Response(200, json={'killed': 2})
        if request.url.path == '/files':
            return httpx.Response(200, json=[{'path': 'a.txt', 'size': 1, 'mtime': 'now'}])
        if request.method == 'PUT':
            return httpx.Response(200, json={'path': 'a.txt', 'size': 1})
        return httpx.Response(200, json={'path': 'a.txt', 'content': 'a', 'binary': False})
    sandbox = Sandbox('http://sandbox')
    await sandbox.client.aclose()
    sandbox.client = httpx.AsyncClient(base_url='http://sandbox', transport=httpx.MockTransport(handler))
    try:
        assert (await sandbox.exec('w', 'echo hi', 2))['exit_code'] == 0
        assert await sandbox.kill('w') == 2
        assert (await sandbox.files('w'))[0]['path'] == 'a.txt'
        assert (await sandbox.read('w', 'a.txt'))['content'] == 'a'
        assert (await sandbox.write('w', 'a.txt', 'a'))['size'] == 1
        assert [r.url.path for r in requests] == ['/exec', '/kill', '/files', '/file', '/file']
        assert json.loads(requests[0].content) == {'workspace': 'w', 'cmd': 'echo hi', 'timeout_s': 2}
        assert requests[3].url.params['workspace'] == 'w'
    finally:
        await sandbox.close()
    fake = FakeSandbox()
    app = create_app(cfg, sandbox=fake)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            cid = (await client.post('/api/conversations', json={})).json()['id']
            await fake.write(cid, 'folder/a.txt', 'abc')
            assert (await client.get(f'/api/conversations/{cid}/files')).json()[0]['size'] == 3
            assert (await client.get(f'/api/conversations/{cid}/files/folder/a.txt')).json()['content'] == 'abc'

async def test_collect_events_fixture(app_client, collect_events):
    cid = (await app_client.post('/api/conversations', json={})).json()['id']
    await app_client.post(f'/api/conversations/{cid}/messages', json={'text': 'hello'})
    result = await collect_events(cid, until=lambda event: event['seq'] == 1)
    assert result[0]['type'] == 'message.created'


def test_the_cookie_path_setting(monkeypatch):
    monkeypatch.delenv('HUDDLE_COOKIE_PATH', raising=False)
    assert cookie_path() == '/'
    monkeypatch.setenv('HUDDLE_COOKIE_PATH', '/huddle/')
    assert cookie_path() == '/huddle/'
    monkeypatch.setenv('HUDDLE_COOKIE_PATH', 'huddle')
    assert cookie_path() == '/huddle/'
    monkeypatch.setenv('HUDDLE_COOKIE_PATH', '')
    assert cookie_path() == '/'


async def test_the_real_sandbox_client_reports_a_missing_file_as_unavailable():
    from huddle.tools import FileUnavailable, Sandbox
    def handler(request):
        if 'missing' in str(request.url):
            return httpx.Response(404, json={'error': 'file not found'})
        return httpx.Response(400, json={'error': 'file exceeds 1 MB'})
    sandbox = Sandbox('http://sandbox', margin_s=1, client=httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                                                             base_url='http://sandbox'))
    with pytest.raises(FileUnavailable) as missing:
        await sandbox.read('w', 'missing.txt')
    assert missing.value.status == 404
    with pytest.raises(FileUnavailable) as large:
        await sandbox.read('w', 'big.csv')
    assert large.value.status == 400 and large.value.reason == 'file exceeds 1 MB'


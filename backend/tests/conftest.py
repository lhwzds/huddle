import asyncio
import json
from copy import deepcopy

import httpx
import pytest

from huddle.config import load_config
from huddle.llm.fake import FakeLLM
from huddle.main import create_app
from huddle.store import Store
from huddle.tools import FileUnavailable

class FakeSandbox:
    def __init__(self):
        self.data = {}
        self.exec_started = asyncio.Event()
        self.exec_release = None
        self.exec_calls = []
        self.killed = []
    async def exec(self, workspace, cmd, timeout_s):
        self.exec_calls.append((workspace, cmd, timeout_s))
        self.exec_started.set()
        if self.exec_release is not None:
            await self.exec_release.wait()
        return {'exit_code': 0, 'stdout': '', 'stderr': '', 'timed_out': False, 'truncated': False}
    async def kill(self, workspace):
        self.killed.append(workspace)
        return 1
    async def files(self, workspace):
        return [{'path': p, 'size': len(s), 'mtime': ''} for p, s in self.data.get(workspace, {}).items()]
    async def read(self, workspace, path):
        if path not in self.data.get(workspace, {}):
            raise FileUnavailable(404)
        return {'path': path, 'content': self.data[workspace][path], 'binary': False}
    async def write(self, workspace, path, content):
        self.data.setdefault(workspace, {})[path] = content
        return {'path': path, 'size': len(content)}

@pytest.fixture
async def store(tmp_path):
    result = Store(tmp_path / 'test.db')
    await result.open()
    yield result
    await result.close()

@pytest.fixture
def cfg(tmp_path):
    cfg = deepcopy(load_config())
    cfg['model'] = 'fake'
    cfg['database'] = str(tmp_path / 'test.db')
    return cfg

@pytest.fixture
async def app_client(cfg):
    model = FakeLLM([])
    app = create_app(cfg, llm_factory=lambda cfg, key: model, sandbox=FakeSandbox())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            yield client

@pytest.fixture
def collect_events(app_client):
    """Collect a real in-process SSE response; httpx ASGITransport buffers streams."""
    async def collect(conv_id, until, timeout=5):
        app = app_client._transport.app
        queue = asyncio.Queue()
        disconnect = asyncio.Event()
        requested = False

        async def receive():
            nonlocal requested
            if not requested:
                requested = True
                return {'type': 'http.request', 'body': b'', 'more_body': False}
            await disconnect.wait()
            return {'type': 'http.disconnect'}

        async def send(message):
            await queue.put(message)

        path = f'/api/conversations/{conv_id}/events'
        scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.3'},
                 'http_version': '1.1', 'method': 'GET', 'scheme': 'http',
                 'path': path, 'raw_path': path.encode(), 'query_string': b'',
                 'headers': [], 'server': ('test', 80), 'client': ('test', 1), 'root_path': ''}
        task = asyncio.create_task(app(scope, receive, send))
        found = []
        try:
            async with asyncio.timeout(timeout):
                while True:
                    message = await queue.get()
                    if message['type'] == 'http.response.start':
                        assert message['status'] == 200
                    if message['type'] != 'http.response.body':
                        continue
                    for line in message.get('body', b'').decode().splitlines():
                        if line.startswith('data: '):
                            event = json.loads(line[6:])
                            found.append(event)
                            if until(event):
                                return found
        finally:
            disconnect.set()
            await task
    return collect

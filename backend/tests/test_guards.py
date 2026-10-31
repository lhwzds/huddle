"""The protection of a public instance: access code, rate limits, caps, budget."""
import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest

from huddle.guards import utc_day
from huddle.llm.fake import FakeLLM, FakeReply, rule, when
from huddle.main import create_app
from conftest import FakeSandbox

SETTINGS = (
    'HUDDLE_ACCESS_CODE',
    'HUDDLE_TRUST_PROXY',
    'HUDDLE_RATE_MESSAGES_PER_MIN',
    'HUDDLE_RATE_MESSAGES_PER_DAY',
    'HUDDLE_RATE_CONVERSATIONS_PER_DAY',
    'HUDDLE_MAX_MESSAGES_PER_CONVERSATION',
    'HUDDLE_MAX_ACTIVE_CONVERSATIONS',
    'HUDDLE_DAILY_TOKEN_BUDGET',
)


def guard_settings(monkeypatch, **values):
    """Only the given settings are on; everything else is off."""
    for name in SETTINGS:
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, str(value))


@asynccontextmanager
async def client_for(cfg, model):
    app = create_app(cfg, llm_factory=lambda cfg, key: model, sandbox=FakeSandbox())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            yield client, app


async def wait_until(check, timeout=3):
    """Wait for a condition that the runtime reaches on its own."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await check():
            return True
        await asyncio.sleep(0.02)
    return False


# -- access ------------------------------------------------------------------

async def test_the_api_needs_the_access_cookie(app_client, monkeypatch):
    guard_settings(monkeypatch, HUDDLE_ACCESS_CODE='test-invite-code')
    client = app_client
    denied = await client.get('/api/groups/startup/agents')
    assert denied.status_code == 401
    assert denied.json() == {'detail': 'access required'}
    # The health check stays open: it tells the frontend that a code is needed.
    assert (await client.get('/api/health')).status_code == 200


async def test_the_right_code_opens_the_api(app_client, monkeypatch):
    guard_settings(monkeypatch, HUDDLE_ACCESS_CODE='test-invite-code')
    client = app_client
    opened = await client.post('/api/access', json={'code': 'test-invite-code'})
    assert opened.status_code == 204
    cookie = opened.headers['set-cookie']
    assert 'huddle_access=test-invite-code' in cookie
    assert 'HttpOnly' in cookie and 'SameSite=lax' in cookie
    assert 'Max-Age=2592000' in cookie
    assert 'Secure' not in cookie
    assert (await client.get('/api/groups/startup/agents')).status_code == 200


async def test_the_cookie_path_follows_the_base_path(app_client, monkeypatch):
    guard_settings(monkeypatch, HUDDLE_ACCESS_CODE='test-invite-code')
    client = app_client
    opened = await client.post('/api/access', json={'code': 'test-invite-code'})
    assert 'Path=/' in opened.headers['set-cookie']
    # The app may live under a prefix; the cookie has to be sent with it.
    monkeypatch.setenv('HUDDLE_COOKIE_PATH', '/huddle/')
    opened = await client.post('/api/access', json={'code': 'test-invite-code'})
    assert 'Path=/huddle/' in opened.headers['set-cookie']


async def test_the_cookie_is_secure_behind_an_https_proxy(app_client, monkeypatch):
    guard_settings(monkeypatch, HUDDLE_ACCESS_CODE='test-invite-code', HUDDLE_TRUST_PROXY=1)
    client = app_client
    opened = await client.post('/api/access', json={'code': 'test-invite-code'},
                               headers={'X-Forwarded-Proto': 'https'})
    assert 'Secure' in opened.headers['set-cookie']


async def test_a_client_cannot_claim_https_without_a_proxy(app_client, monkeypatch):
    guard_settings(monkeypatch, HUDDLE_ACCESS_CODE='test-invite-code')
    client = app_client
    opened = await client.post('/api/access', json={'code': 'test-invite-code'},
                               headers={'X-Forwarded-Proto': 'https'})
    assert 'Secure' not in opened.headers['set-cookie']


async def test_a_wrong_code_is_refused_and_the_client_is_locked_out(app_client, monkeypatch):
    guard_settings(monkeypatch, HUDDLE_ACCESS_CODE='test-invite-code')
    client = app_client
    for _ in range(10):
        wrong = await client.post('/api/access', json={'code': 'nope'})
        assert wrong.status_code == 401
    locked = await client.post('/api/access', json={'code': 'nope'})
    assert locked.status_code == 429
    assert locked.json()['detail'] == 'too many attempts'
    assert int(locked.headers['Retry-After']) > 0
    # The right code still opens the app: only wrong guesses are counted.
    assert (await client.post('/api/access', json={'code': 'test-invite-code'})).status_code == 204


async def test_the_api_is_open_when_no_code_is_set(app_client, monkeypatch):
    guard_settings(monkeypatch)
    client = app_client
    assert (await client.get('/api/groups/startup/agents')).status_code == 200
    assert (await client.post('/api/access', json={'code': 'anything'})).status_code == 204


# -- rate limits -------------------------------------------------------------

async def test_too_many_messages_per_minute(app_client, monkeypatch):
    guard_settings(monkeypatch, HUDDLE_RATE_MESSAGES_PER_MIN=2)
    client = app_client
    cid = (await client.post('/api/conversations', json={})).json()['id']
    for _ in range(2):
        assert (await client.post(f'/api/conversations/{cid}/messages', json={'text': 'hi'})).status_code == 202
    refused = await client.post(f'/api/conversations/{cid}/messages', json={'text': 'hi'})
    assert refused.status_code == 429
    body = refused.json()
    assert body['detail'] == 'slow down'
    assert body['retry_after'] > 0
    assert int(refused.headers['Retry-After']) == body['retry_after']
    # The stop is never rate limited.
    assert (await client.post(f'/api/conversations/{cid}/stop')).status_code == 202


async def test_too_many_messages_per_day(app_client, monkeypatch):
    guard_settings(monkeypatch, HUDDLE_RATE_MESSAGES_PER_DAY=1)
    client = app_client
    cid = (await client.post('/api/conversations', json={})).json()['id']
    assert (await client.post(f'/api/conversations/{cid}/messages', json={'text': 'hi'})).status_code == 202
    refused = await client.post(f'/api/conversations/{cid}/messages', json={'text': 'hi'})
    assert refused.status_code == 429 and refused.json()['detail'] == 'slow down'


async def test_too_many_conversations_per_day(app_client, monkeypatch):
    guard_settings(monkeypatch, HUDDLE_RATE_CONVERSATIONS_PER_DAY=1)
    client = app_client
    assert (await client.post('/api/conversations', json={})).status_code == 201
    refused = await client.post('/api/conversations', json={})
    assert refused.status_code == 429 and refused.json()['detail'] == 'slow down'
    assert int(refused.headers['Retry-After']) > 0


# -- caps --------------------------------------------------------------------

async def test_a_full_conversation_is_refused(app_client, monkeypatch):
    guard_settings(monkeypatch, HUDDLE_MAX_MESSAGES_PER_CONVERSATION=2)
    client = app_client
    cid = (await client.post('/api/conversations', json={})).json()['id']
    for _ in range(2):
        assert (await client.post(f'/api/conversations/{cid}/messages', json={'text': 'hi'})).status_code == 202
    refused = await client.post(f'/api/conversations/{cid}/messages', json={'text': 'hi'})
    assert refused.status_code == 429 and refused.json()['detail'] == 'conversation full'


async def test_a_busy_server_is_refused(cfg, monkeypatch):
    guard_settings(monkeypatch, HUDDLE_MAX_ACTIVE_CONVERSATIONS=1)
    model = FakeLLM([
        rule(when(profile='decide'),
             FakeReply('{"speak": true, "point": "hi", "effort": "light", "addresses": []}', delay=0)),
        rule(when(profile='light'), FakeReply('a long answer ' * 8, chunk=1, delay=0.02)),
    ])
    async with client_for(cfg, model) as (client, app):
        first = (await client.post('/api/conversations', json={})).json()['id']
        second = (await client.post('/api/conversations', json={})).json()['id']
        await client.post(f'/api/conversations/{first}/messages', json={'text': 'Hello everyone'})
        assert await wait_until(lambda: _active(app, first))
        refused = await client.post(f'/api/conversations/{second}/messages', json={'text': 'Hi'})
        assert refused.status_code == 429 and refused.json()['detail'] == 'busy'
        # The conversation that is already working may steer.
        assert (await client.post(f'/api/conversations/{first}/messages',
                                  json={'text': 'shorter'})).status_code == 202


def _active(app, conv_id):
    async def check():
        return app.state.orchestrator.is_active(conv_id)
    return check()


# -- the daily budget --------------------------------------------------------

async def test_the_budget_blocks_new_messages_but_not_the_stop(app_client, monkeypatch):
    guard_settings(monkeypatch, HUDDLE_DAILY_TOKEN_BUDGET=1)
    client = app_client
    app = client._transport.app
    cid = (await client.post('/api/conversations', json={})).json()['id']
    assert (await client.post(f'/api/conversations/{cid}/messages', json={'text': 'hi'})).status_code == 202
    assert await wait_until(lambda: _used(app))
    refused = await client.post(f'/api/conversations/{cid}/messages', json={'text': 'again'})
    assert refused.status_code == 429 and refused.json()['detail'] == 'resting'
    # A running answer is not cut off, and the stop still works.
    assert (await client.post(f'/api/conversations/{cid}/stop')).status_code == 202
    health = (await client.get('/api/health')).json()
    assert health['budget_left'] == 0


def _used(app):
    async def check():
        return await app.state.store.usage_tokens(utc_day()) > 0
    return check()


async def test_the_health_check_reports_the_budget(app_client, monkeypatch):
    guard_settings(monkeypatch, HUDDLE_DAILY_TOKEN_BUDGET=100000)
    client = app_client
    health = (await client.get('/api/health')).json()
    assert health['budget_left'] == 100000
    cid = (await client.post('/api/conversations', json={})).json()['id']
    await client.post(f'/api/conversations/{cid}/messages', json={'text': 'hi'})
    assert await wait_until(lambda: _used(client._transport.app))
    health = (await client.get('/api/health')).json()
    assert 0 < health['budget_left'] < 100000


# -- the client address ------------------------------------------------------

async def post_from(client, cid, forwarded):
    return await client.post(f'/api/conversations/{cid}/messages', json={'text': 'hi'},
                             headers={'X-Forwarded-For': forwarded})


async def test_behind_one_proxy_the_address_it_saw_is_the_client(app_client, monkeypatch):
    # client -> proxy (appends the client) -> nginx (appends the proxy) -> backend
    guard_settings(monkeypatch, HUDDLE_RATE_MESSAGES_PER_MIN=1, HUDDLE_TRUST_PROXY=1)
    client = app_client
    cid = (await client.post('/api/conversations', json={})).json()['id']
    assert (await post_from(client, cid, '1.2.3.4, 10.0.0.1')).status_code == 202
    assert (await post_from(client, cid, '5.6.7.8, 10.0.0.1')).status_code == 202
    assert (await post_from(client, cid, '1.2.3.4, 10.0.0.1')).status_code == 429


async def test_a_client_cannot_spoof_its_address_behind_a_proxy(app_client, monkeypatch):
    guard_settings(monkeypatch, HUDDLE_RATE_MESSAGES_PER_MIN=1, HUDDLE_TRUST_PROXY=1)
    client = app_client
    cid = (await client.post('/api/conversations', json={})).json()['id']
    assert (await post_from(client, cid, '9.9.9.1, 1.2.3.4, 10.0.0.1')).status_code == 202
    assert (await post_from(client, cid, '9.9.9.2, 1.2.3.4, 10.0.0.1')).status_code == 429


async def test_without_a_proxy_the_address_nginx_saw_is_the_client(app_client, monkeypatch):
    # client -> nginx (appends the client) -> backend; whatever the client wrote itself is ignored
    guard_settings(monkeypatch, HUDDLE_RATE_MESSAGES_PER_MIN=1)
    client = app_client
    cid = (await client.post('/api/conversations', json={})).json()['id']
    assert (await post_from(client, cid, '9.9.9.1, 1.2.3.4')).status_code == 202
    assert (await post_from(client, cid, '5.6.7.8')).status_code == 202
    assert (await post_from(client, cid, '9.9.9.2, 1.2.3.4')).status_code == 429


# -- everything off ----------------------------------------------------------

async def test_nothing_is_limited_when_the_settings_are_unset(app_client, monkeypatch):
    guard_settings(monkeypatch)
    client = app_client
    cid = (await client.post('/api/conversations', json={})).json()['id']
    for _ in range(30):
        assert (await client.post(f'/api/conversations/{cid}/messages',
                                  json={'text': 'hi'})).status_code == 202
    assert (await client.get('/api/health')).json() == {'ok': True, 'model': 'fake'}


async def test_the_older_on_off_setting_means_one_proxy(app_client, monkeypatch):
    guard_settings(monkeypatch, HUDDLE_RATE_MESSAGES_PER_MIN=1, HUDDLE_TRUST_PROXY='true')
    client = app_client
    cid = (await client.post('/api/conversations', json={})).json()['id']
    assert (await post_from(client, cid, '1.2.3.4, 10.0.0.1')).status_code == 202
    assert (await post_from(client, cid, '5.6.7.8, 10.0.0.1')).status_code == 202

"""HTTP/SSE evaluation harness.

The configuration is the server's own: the harness imports the backend's
config package (`huddle.config`) instead of carrying a copy of the
interpolation and of the profile params.
"""
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from urllib.parse import quote

import httpx

from huddle.config import load_config, params_for

from .core import IDLE_POLL_INTERVAL_S, check, idle_poll, metrics, replay, transcript
from .prompts import JUDGE_MAX_TOKENS, JUDGE_SYSTEM, judge_user

# The judge talks to a model of the config: Chat Completions or Responses.
JUDGE_PROVIDERS = ('openai_chat', 'openai_responses')
# The server under test: $HUDDLE_EVAL_BASE, else the local backend.
DEFAULT_BASE = 'http://localhost:8080'


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def default_base(env=None):
    """The server under test: `$HUDDLE_EVAL_BASE`, else the local backend."""
    env = os.environ if env is None else env
    return (env.get('HUDDLE_EVAL_BASE') or '').strip() or DEFAULT_BASE


def default_judge():
    """The judge model key by default: the active model of the server's config."""
    try:
        return load_config().get('model')
    except (OSError, ValueError):
        return None


def judge_key(cfg, requested=None):
    """The judge model key: the requested one, else the config's active model."""
    if requested:
        return requested
    key = cfg.get('model')
    if not key:
        raise ValueError('the config has no active model: pass --judge')
    return key


def api_key(model_cfg, key=None):
    """The API key of a model entry: `api_key_file` first, then `api_key_env`."""
    env = model_cfg.get('api_key_env')
    file = model_cfg.get('api_key_file')
    if file:
        path = Path(str(file)).expanduser()
        try:
            value = path.read_text(encoding='utf-8').strip()
        except OSError:
            value = ''
        if value:
            return value
        if env and (os.environ.get(env) or '').strip():
            return os.environ[env].strip()
        raise ValueError(f'Missing judge API key file: {path}')
    if not env:
        name = f'judge model {key}' if key else 'the judge model'
        raise ValueError(f'{name} has neither api_key_env nor api_key_file')
    value = (os.environ.get(env) or '').strip()
    if not value:
        raise ValueError(f'Missing judge API key environment variable: {env}')
    return value


def judge_setup(requested=None, cfg=None):
    """The judge of a run: the model key and the (entry, API key) pair.

    The model is the one that was asked for, else the active model of the
    server's config.
    """
    cfg = cfg if cfg is not None else load_config()
    key = judge_key(cfg, requested)
    return key, config_model(cfg, key)


def config_model(cfg, key):
    """The judge model of a config: its entry and its API key.

    `cfg` is a loaded config, or the path of a config file.
    """
    if isinstance(cfg, (str, Path)):
        cfg = load_config(cfg)
    models = cfg.get('models') or {}
    if key not in models:
        raise ValueError(f'unknown model key: {key}')
    model_cfg = models[key]
    provider = model_cfg.get('provider') or 'openai_chat'
    if provider not in JUDGE_PROVIDERS:
        raise ValueError(f'judge {key} is not an openai_chat or openai_responses model')
    return model_cfg, api_key(model_cfg, key)


def _verdict_json(text):
    """The JSON object of the judge's answer (a code fence around it is fine)."""
    text = (text or '').strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[-1] if '\n' in text else ''
        if text.endswith('```'):
            text = text[:-3].strip()
    return json.loads(text)


def _chat_text(payload):
    """The text of a Chat Completions answer."""
    choices = payload.get('choices') or [{}]
    return ((choices[0] or {}).get('message') or {}).get('content') or ''


def _responses_text(payload):
    """The text of a Responses answer: the output items' output_text parts."""
    text = payload.get('output_text')
    if isinstance(text, str) and text.strip():
        return text
    if isinstance(text, list):
        return ''.join(part for part in text if isinstance(part, str))
    parts = []
    for item in payload.get('output') or []:
        if not isinstance(item, dict):
            continue
        for content in item.get('content') or []:
            if isinstance(content, dict) and isinstance(content.get('text'), str):
                parts.append(content['text'])
    return ''.join(parts)


async def judge_statement(client, config, statement, text):
    cfg, key = config
    provider = cfg.get('provider') or 'openai_chat'
    if provider == 'openai_responses':
        # The Responses API: instructions and input, nothing is stored.
        body = {'model': cfg['model'], 'instructions': JUDGE_SYSTEM,
                'input': judge_user(text, statement), 'store': False, 'stream': False}
        url, token_field, read = cfg['base_url'].rstrip('/') + '/responses', 'max_output_tokens', _responses_text
    elif provider == 'openai_chat':
        body = {'model': cfg['model'], 'messages': [
            {'role': 'system', 'content': JUDGE_SYSTEM},
            {'role': 'user', 'content': judge_user(text, statement)}],
            'stream': False, 'response_format': {'type': 'json_object'}}
        url, token_field, read = cfg['base_url'].rstrip('/') + '/chat/completions', 'max_tokens', _chat_text
    else:
        raise ValueError(f'judge provider {provider!r} is not supported')
    # The judge reasons: it gets the deep profile params of its model.
    body.update(params_for(cfg, 'deep'))
    verdict = None
    # A judge that runs out of tokens returns cut-off JSON; retry once with more room.
    for max_tokens in (JUDGE_MAX_TOKENS, JUDGE_MAX_TOKENS * 3):
        body[token_field] = max_tokens
        response = await client.post(url, headers={'Authorization': f'Bearer {key}'}, json=body, timeout=120)
        response.raise_for_status()
        try:
            verdict = _verdict_json(read(response.json()))
            break
        except (json.JSONDecodeError, TypeError):
            continue
    if verdict is None:
        raise ValueError('judge returned no valid JSON')
    if not isinstance(verdict.get('verdict'), bool) or not isinstance(verdict.get('reason'), str):
        raise ValueError('judge response must have boolean verdict and string reason')
    return {'statement': statement, **verdict}


class Session:
    def __init__(self, client, conv_id):
        self.client, self.path = client, f'/api/conversations/{conv_id}'
        self.events, self.sends, self.reloads = [], [], []
        self.idle_timeout_s = 90
        self.ready = asyncio.Event()
        self.stream_error = None
        self.reconnect_count = 0
        self.stream_task = None

    async def request(self, method, path, **kwargs):
        r = await self.client.request(method, path, **kwargs); r.raise_for_status(); return r.json()

    async def stream(self):
        cursor = 0
        while True:
            try:
                async with self.client.stream('GET', self.path + '/events', params={'after': cursor}, timeout=httpx.Timeout(connect=10, read=None, write=10, pool=10)) as r:
                    r.raise_for_status(); self.ready.set()
                    data = []
                    async for line in r.aiter_lines():
                        if not line:
                            if data:
                                e = json.loads('\n'.join(data)); data = []
                                if e['seq'] > cursor:
                                    if e['seq'] != cursor + 1:
                                        raise ValueError(f'SSE sequence gap after {cursor}: {e["seq"]}')
                                    e['received_at'] = time.monotonic(); e['received_at_utc'] = now()
                                    self.events.append(e); cursor = e['seq']
                        elif line.startswith('data:'):
                            data.append(line[5:].lstrip(' '))
                await self._reconnect()
                if self.stream_error: return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.stream_error = None
                await self._reconnect()
                if self.stream_error: return

    async def _reconnect(self):
        self.reconnect_count += 1
        if self.reconnect_count > 20:
            self.stream_error = RuntimeError('SSE reconnect limit exceeded (20)')
            self.ready.set()
            return
        await asyncio.sleep(.5)

    def ensure_stream(self):
        if self.stream_task is not None and self.stream_task.done() and self.stream_error is None:
            try:
                self.stream_error = self.stream_task.exception() or RuntimeError("SSE stream task ended")
            except asyncio.CancelledError:
                self.stream_error = RuntimeError("SSE stream task ended")
        if self.stream_error:
            raise RuntimeError(f'SSE stream failed: {self.stream_error}') from self.stream_error

    async def sync(self, seq, timeout=30):
        async def wait():
            while not self.events or self.events[-1]['seq'] < seq:
                self.ensure_stream(); await asyncio.sleep(.02)
        if seq:
            await asyncio.wait_for(wait(), timeout)

    async def snapshot(self):
        snap = await self.request('GET', self.path)
        await self.sync(snap['last_seq'])
        return snap

    async def reload(self):
        snap = await self.snapshot()
        # Independent SSE replay, bounded by the snapshot's consistent sequence.
        events = []
        if snap['last_seq']:
            async def read():
                async with self.client.stream('GET', self.path + '/events', params={'after': 0}, timeout=httpx.Timeout(connect=10, read=None, write=10, pool=10)) as r:
                    r.raise_for_status(); data = []
                    async for line in r.aiter_lines():
                        if not line:
                            if data:
                                e = json.loads('\n'.join(data)); data = []
                                if e['seq'] != len(events) + 1:
                                    raise ValueError('reload replay sequence gap')
                                events.append(e)
                                if e['seq'] == snap['last_seq']:
                                    return
                        elif line.startswith('data:'):
                            data.append(line[5:].lstrip(' '))
            await asyncio.wait_for(read(), 30)
        self.reloads.append({'snapshot': snap, 'events': events})
        return snap

    async def say(self, text):
        send = {'kind': 'say', 'text': text, 'turn': 1 + sum(s['kind'] == 'say' for s in self.sends), 'time': time.monotonic(), 'sent_at_utc': now()}
        self.sends.append(send)
        response = await self.request('POST', self.path + '/messages', json={'text': text})
        if response['message']['turn'] != send['turn']:
            raise ValueError('server returned unexpected user turn')
        send['message_id'] = response['message']['id']; send['returned_at'] = time.monotonic()

    async def stop(self):
        self.sends.append({'kind': 'stop', 'time': time.monotonic(), 'sent_at_utc': now()})
        await self.request('POST', self.path + '/stop')
        self.sends[-1]['returned_at'] = time.monotonic()

    async def wait(self, value):
        if isinstance(value, dict) and 'ms' in value:
            await asyncio.sleep(value['ms']/1000); return
        streak = 0
        async def poll():
            nonlocal streak
            while True:
                self.ensure_stream()
                if value == 'idle':
                    self.ensure_stream()
                    snap = await self.snapshot()
                    last_send = max((s.get('returned_at', s['time']) for s in self.sends if s['kind'] in ('say', 'stop')), default=0)
                    # Idle is the server's word: no active work, nothing typing,
                    # and the last user message or stop is at least 1.5 s old,
                    # for two polls in a row.
                    settled, streak = idle_poll(snap, last_send, time.monotonic(), streak)
                    if settled:
                        return
                    await asyncio.sleep(IDLE_POLL_INTERVAL_S)
                    continue
                messages = replay(self.events)
                candidates = [m for m in messages if m['author'] == value['agent']]
                if candidates and (value.get('message_started') or any(len(m['text']) >= value['chars'] for m in candidates)):
                    return
                await asyncio.sleep(.05)
        await asyncio.wait_for(poll(), self.idle_timeout_s if value == 'idle' else 180)


async def run_case(client, case, repeat, judge_config, quiet_ms=0, idle_timeout_s=90):
    """Run one case. `quiet_ms` is accepted and ignored: idle no longer waits
    for a quiet period, it waits for the conversation to stop being active."""
    result = {'id': case['id'], 'category': case['category'], 'repeat': repeat, 'steps': [], 'checks': [], 'judge': [], 'metrics': {}, 'events': [], 'sends': [], 'reconnect_count': 0, 'error': None}
    session = None; task = None
    try:
        body = {'title': f'eval: {case["id"]}'}
        if case.get('group'):
            body['group'] = case['group']  # the group whose members take part in this case
        r = await client.post('/api/conversations', json=body); r.raise_for_status()
        result['conversation_id'] = r.json()['id']
        session = Session(client, result['conversation_id']); session.idle_timeout_s = idle_timeout_s; task = asyncio.create_task(session.stream()); session.stream_task = task
        await asyncio.wait_for(session.ready.wait(), 30); session.ensure_stream()
        for step in case['steps']:
            name, value = next(iter(step.items())); entry = {'step': step, 'started_at': now()}; result['steps'].append(entry)
            try:
                if name == 'say': await session.say(value)
                elif name == 'stop': await session.stop()
                elif name == 'wait': await session.wait(value)
                elif name == 'reload':
                    await session.reload()
                    comparison = check('reload_identical', True, session.events, session.sends, reloads=[session.reloads[-1]])
                    entry.update({'pass': comparison['pass'], 'detail': comparison['detail']})
                elif name == 'burst':
                    start = time.monotonic()
                    for i, text in enumerate(value['texts']):
                        await asyncio.sleep(max(0, start + i * value['interval_ms']/1000 - time.monotonic()))
                        await session.say(text)
                entry.setdefault('pass', True)
            except Exception as exc:
                entry.update({'pass': False, 'error': f'{type(exc).__name__}: {exc}'}); raise
            finally:
                entry['finished_at'] = now()
        await session.wait('idle') # apply the same settling/quiet rule before final snapshot
        snapshot = await session.reload() # final comparison always uses a fresh replay
        files = None
        if any('file_exists' in c for c in case.get('checks', [])):
            files = await session.request('GET', session.path + '/files')
        # Freeze at the consistent final snapshot for comparisons and analysis.
        events = [e for e in session.events if e['seq'] <= snapshot['last_seq']]
        for item in case.get('checks', []):
            name, args = next(iter(item.items()))
            result['checks'].append(check(name, args, events, session.sends, reloads=session.reloads, files=files))
        result['metrics'] = metrics(events, session.sends)
        result['snapshot'] = snapshot
        if judge_config:
            text = transcript(events, session.sends, snapshot['agents']); result['transcript'] = text
            for statement in case.get('judge', []):
                try:
                    result['judge'].append(await judge_statement(client, judge_config, statement, text))
                except Exception as exc:
                    result['judge'].append({'statement': statement, 'verdict': None, 'reason': f'{type(exc).__name__}: {exc}'})
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        if task:
            task.cancel()
            try: await task
            except asyncio.CancelledError: pass
        if session:
            result['reconnect_count'] = session.reconnect_count
            result['events'] = session.events; result['sends'] = session.sends
            if not result['metrics']:
                try: result['metrics'] = metrics(session.events, session.sends)
                except Exception: pass
    return result

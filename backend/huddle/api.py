"""HTTP endpoints and gap-free event replay/live streaming."""
import asyncio
import json
import os
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from .agents import TooManyAgents, UnknownGroup
from .agents import public as public_agent
from .tools import FileUnavailable
from .guards import (ACCESS_COOKIE, ACCESS_MAX_AGE, DAY, MINUTE, LimitError, access_code,
                     is_https, max_active_conversations, max_messages_per_conversation,
                     rate_agents_per_day, rate_conversations_per_day, rate_messages_per_day,
                     rate_messages_per_min)

class ConversationBody(BaseModel):
    title: str | None = None
    group: str | None = None

class MessageBody(BaseModel):
    text: str

class AccessBody(BaseModel):
    code: str = ''

router = APIRouter(prefix='/api')

def cookie_path() -> str:
    """The path of the access cookie: where the app is served.

    The app may live under a prefix ('/huddle/'); the cookie has to be
    sent with every request under it.
    """
    raw = (os.environ.get('HUDDLE_COOKIE_PATH') or '').strip()
    if not raw:
        return '/'
    if not raw.startswith('/'):
        raw = '/' + raw
    if not raw.endswith('/'):
        raw = raw + '/'
    return raw

def _public_agent(agent):
    """An agent as the chat shows it (soul and memory stay out of the snapshot)."""
    data = public_agent(agent)
    return {k: data[k] for k in ('key', 'name', 'role', 'color', 'lens')}

async def _exists(request, conv_id):
    if await request.app.state.store.get_conversation(conv_id) is None:
        raise HTTPException(status_code=404, detail='Conversation not found')

@router.get('/health')
async def health(request: Request):
    body = {'ok': True, 'model': request.app.state.cfg['model']}
    left = await request.app.state.guards.budget_left()
    if left is not None:
        body['budget_left'] = left
    return body

@router.post('/access', status_code=204)
async def access(request: Request, body: AccessBody):
    """Exchange the invite code for the access cookie."""
    guards = request.app.state.guards
    accepted, wait = guards.check_code(request, body.code)
    if not accepted:
        if wait is not None:
            raise LimitError('too many attempts', retry_after=wait)
        raise HTTPException(status_code=401, detail='invalid code')
    response = Response(status_code=204)
    code = access_code()
    if code:
        response.set_cookie(ACCESS_COOKIE, code, max_age=ACCESS_MAX_AGE, httponly=True,
                            samesite='lax', secure=is_https(request), path=cookie_path())
    return response

class AgentBody(BaseModel):
    name: str | None = None
    role: str | None = None
    color: str | None = None
    lens: str | None = None
    soul: str | None = None
    memory: str | None = None


def _registry(request):
    return request.app.state.registry


@router.get('/config')
async def client_config(request: Request):
    """What the web app needs to know: the default group, field limits and avatar colors."""
    cfg = request.app.state.cfg
    fields = cfg['limits']['fields']
    limits = {k: fields[k] for k in ('agents_per_group', 'name', 'role', 'lens', 'soul', 'memory',
                                    'group_name', 'group_description', 'group_common')}
    limits['message'] = cfg['limits']['message_chars']
    return {'default_group': _registry(request).default, 'limits': limits, 'palette': cfg['palette']}


class GroupBody(BaseModel):
    name: str | None = None
    description: str | None = None
    common: str | None = None


def _group(request, group):
    try:
        return _registry(request).group_key(group)
    except UnknownGroup:
        raise HTTPException(status_code=404, detail='Group not found')


@router.get('/groups')
async def groups(request: Request):
    return _registry(request).groups()

@router.put('/groups/{group}')
async def update_group(request: Request, group: str, body: GroupBody):
    group = _group(request, group)
    try:
        return await _registry(request).update_group(group, body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

@router.post('/groups/{group}/reset')
async def reset_group(request: Request, group: str):
    group = _group(request, group)
    await _registry(request).reset(group)
    return _registry(request).group(group)

@router.get('/groups/{group}/agents')
async def group_agents(request: Request, group: str):
    group = _group(request, group)
    return [public_agent(agent) for agent in _registry(request).all(group)]

@router.post('/groups/{group}/agents', status_code=201)
async def create_agent(request: Request, group: str, body: AgentBody):
    group = _group(request, group)
    request.app.state.guards.rate_limit('agents-day', request, rate_agents_per_day(), DAY)
    try:
        agent = await _registry(request).create(group, body.model_dump(exclude_none=True))
    except TooManyAgents:
        raise HTTPException(status_code=409, detail='too many agents')
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return public_agent(agent)

@router.put('/groups/{group}/agents/{key}')
async def update_agent(request: Request, group: str, key: str, body: AgentBody):
    group = _group(request, group)
    try:
        agent = await _registry(request).update(group, key, body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if agent is None:
        raise HTTPException(status_code=404, detail='Agent not found')
    return public_agent(agent)

@router.get('/conversations')
async def list_conversations(request: Request):
    return await request.app.state.store.list_conversations()

@router.post('/conversations', status_code=201)
async def create_conversation(request: Request, body: ConversationBody = ConversationBody()):
    request.app.state.guards.rate_limit('conversations-day', request,
                                        rate_conversations_per_day(), DAY)
    group = _group(request, body.group)
    return await request.app.state.store.create_conversation(body.title, group)

@router.get('/conversations/{conv_id}')
async def snapshot(request: Request, conv_id: str):
    await _exists(request, conv_id)
    app = request.app
    orchestrator = app.state.orchestrator
    group = await orchestrator.ensure_group(conv_id)
    def state():
        current = orchestrator.agents_state(conv_id)
        return [_public_agent(agent) | current[agent.key] for agent in orchestrator.members(conv_id)]
    def active():
        return orchestrator.is_active(conv_id)
    # The store reads both callables while it holds the conversation lock.
    snap = await app.state.store.snapshot(conv_id, state, active)
    info = orchestrator.registry.group(group)
    snap['group'] = {'key': group, 'name': info['name']}
    snap['conversation']['group'] = group
    return snap

async def _check_message(request, conv_id):
    """The limits that guard a new user message."""
    app = request.app
    guards = app.state.guards
    guards.rate_limit('messages-minute', request, rate_messages_per_min(), MINUTE)
    guards.rate_limit('messages-day', request, rate_messages_per_day(), DAY)
    cap = max_messages_per_conversation()
    if cap:
        messages = await app.state.store.messages(conv_id)
        used = sum(1 for m in messages if m['author'] == 'user')
        if used >= cap:
            raise LimitError('conversation full')
    busy_cap = max_active_conversations()
    if busy_cap:
        orchestrator = app.state.orchestrator
        if not orchestrator.is_active(conv_id):
            others = [c for c in orchestrator.active_conversations() if c != conv_id]
            if len(others) >= busy_cap:
                raise LimitError('busy')
    if await guards.budget_spent():
        raise LimitError('resting')

@router.post('/conversations/{conv_id}/messages', status_code=202)
async def message(request: Request, conv_id: str, body: MessageBody):
    await _exists(request, conv_id)
    text = body.text.strip()
    limit = request.app.state.cfg['limits']['message_chars']
    if not 1 <= len(text) <= limit:
        raise HTTPException(status_code=422, detail=f'text must contain 1 to {limit} characters')
    await _check_message(request, conv_id)
    return {'message': await request.app.state.orchestrator.on_user_message(conv_id, text)}

@router.post('/conversations/{conv_id}/stop', status_code=202)
async def stop(request: Request, conv_id: str):
    await _exists(request, conv_id)
    await request.app.state.orchestrator.on_stop(conv_id)
    return {}

@router.get('/conversations/{conv_id}/files')
async def files(request: Request, conv_id: str):
    await _exists(request, conv_id)
    try:
        return await request.app.state.sandbox.files(conv_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail='Sandbox unavailable') from exc

@router.get('/conversations/{conv_id}/files/{path:path}')
async def read_file(request: Request, conv_id: str, path: str):
    await _exists(request, conv_id)
    try:
        return await request.app.state.sandbox.read(conv_id, path)
    except FileUnavailable as exc:
        if exc.status == 404:
            raise HTTPException(status_code=404, detail='File not found') from exc
        raise HTTPException(status_code=400, detail=exc.reason or 'File cannot be shown') from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail='Sandbox unavailable') from exc

@router.get('/conversations/{conv_id}/events')
async def events(request: Request, conv_id: str, after: Annotated[int | None, Query(ge=0)] = None, last_event_id: Annotated[str | None, Header()] = None):
    await _exists(request, conv_id)
    if after is None:
        try:
            after = max(0, int(last_event_id)) if last_event_id else 0
        except ValueError:
            raise HTTPException(status_code=400, detail='Invalid Last-Event-ID')
    store = request.app.state.store
    bus = store.bus
    async def stream():
        # Subscribe before reading the log; replay and the queue may overlap.
        queue = bus.subscribe(conv_id)
        cursor = after
        try:
            replay = await store.events_after(conv_id, cursor)
            for event in replay:
                if event['seq'] > cursor:
                    cursor = event['seq']
                    yield format_event(event)
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=request.app.state.cfg['limits']['sse_ping_s'])
                except TimeoutError:
                    yield ': ping\n\n'
                    continue
                if event['seq'] > cursor:
                    cursor = event['seq']
                    yield format_event(event)
        finally:
            bus.unsubscribe(conv_id, queue)
    return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

def format_event(event):
    return f"id: {event['seq']}\nevent: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

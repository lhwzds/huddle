"""Editable agents: the registry, the API, the remember tool, and new agents joining the chat."""
import json

import pytest

from huddle.agents import AgentRegistry, TooManyAgents, UnknownGroup
from huddle.llm import ToolCall
from huddle.llm.fake import FakeReply, rule, when
from huddle.store import Store
from test_runtime import answer, quiet, runtime, utility  # noqa: F401 (runtime is a fixture)

SOUL = "You are calm and practical. You talk about operations."


async def test_seed_create_update_persist_and_reset(cfg, tmp_path):
    store = await Store(tmp_path / 'a.db').open()
    try:
        registry = AgentRegistry(store, cfg)
        await registry.load()
        assert [g['key'] for g in registry.groups()] == ['startup', 'family', 'launch']
        assert [a.key for a in registry.all()] == ['mira', 'theo', 'juno']
        assert len(registry.all('family')) == 5 and len(registry.all('launch')) == 10
        assert registry.get('startup', 'mira').persona and registry.get('startup', 'mira').knowledge

        ops = await registry.create('startup', {'name': 'Ops Lead', 'role': 'COO', 'lens': 'operations', 'soul': SOUL,
                                                'memory': 'Warehouse B is at capacity.'})
        assert ops.key == 'ops-lead' and ops.color.startswith('#') and ops.common
        version = registry.version
        await registry.update('startup', 'mira', {'soul': 'New soul.', 'memory': 'New memory.'})
        await registry.update_group('family', {'common': 'The Rossis live in Lisbon now.'})
        assert registry.version > version

        # A restart reads the edited agents and groups back from the database.
        again = AgentRegistry(store, cfg)
        await again.load()
        assert [a.key for a in again.all('startup')] == ['mira', 'theo', 'juno', 'ops-lead']
        assert (again.get('startup', 'mira').persona, again.get('startup', 'mira').knowledge) == ('New soul.', 'New memory.')
        assert again.group('family')['common'] == 'The Rossis live in Lisbon now.'
        assert all(a.common == 'The Rossis live in Lisbon now.' for a in again.all('family'))

        await again.reset('startup')
        assert [a.key for a in again.all('startup')] == ['mira', 'theo', 'juno']
        assert again.get('startup', 'mira').persona != 'New soul.'
        assert again.group('family')['common'] == 'The Rossis live in Lisbon now.'   # other groups untouched
    finally:
        await store.close()


@pytest.mark.parametrize('fields,message', [
    ({'name': 'Mira', 'soul': SOUL}, 'already someone'),
    ({'name': '1abc', 'soul': SOUL}, 'Name'),
    ({'name': 'Zed', 'soul': ''}, 'Soul'),
    ({'name': 'Zed', 'soul': SOUL, 'color': 'red'}, 'Color'),
    ({'name': 'Zed', 'soul': 'x' * 6001}, 'at most'),
    ({'soul': SOUL}, 'name'),
])
async def test_create_validation(cfg, store, fields, message):
    registry = AgentRegistry(store, cfg)
    await registry.load()
    with pytest.raises(ValueError, match=message):
        await registry.create('startup', fields)


async def test_same_name_in_another_group_is_fine_and_cap_is_ten(cfg, store):
    registry = AgentRegistry(store, cfg)
    await registry.load()
    assert (await registry.create('family', {'name': 'Mira', 'soul': SOUL})).key == 'mira'
    cap = cfg['limits']['fields']['agents_per_group']
    for i in range(cap - 6):
        await registry.create('family', {'name': f'Extra {chr(65 + i)}', 'soul': SOUL})
    assert len(registry.all('family')) == cap == 10
    with pytest.raises(TooManyAgents):
        await registry.create('family', {'name': 'One More', 'soul': SOUL})
    with pytest.raises(UnknownGroup):
        registry.all('nope')


async def test_api_crud_and_snapshot(app_client):
    agents = (await app_client.get('/api/groups/startup/agents')).json()
    assert [a['key'] for a in agents] == ['mira', 'theo', 'juno'] and 'soul' in agents[0] and 'memory' in agents[0]
    r = await app_client.post('/api/groups/startup/agents', json={'name': 'Zed', 'role': 'Ops', 'lens': 'logistics', 'soul': SOUL})
    assert r.status_code == 201 and r.json()['key'] == 'zed'
    assert (await app_client.post('/api/groups/startup/agents', json={'name': 'Zed', 'soul': SOUL})).status_code == 422
    r = await app_client.put('/api/groups/startup/agents/zed', json={'memory': 'Knows the trucks.'})
    assert r.status_code == 200 and r.json()['memory'] == 'Knows the trucks.'
    assert (await app_client.put('/api/groups/startup/agents/nobody', json={'role': 'x'})).status_code == 404
    cid = (await app_client.post('/api/conversations', json={})).json()['id']
    snap = (await app_client.get(f'/api/conversations/{cid}')).json()
    zed = next(a for a in snap['agents'] if a['key'] == 'zed')
    assert zed['typing'] is False and 'soul' not in zed
    reset = (await app_client.post('/api/groups/startup/reset')).json()
    assert reset['agent_count'] == 3
    assert [a['key'] for a in (await app_client.get('/api/groups/startup/agents')).json()] == ['mira', 'theo', 'juno']


async def test_remember_tool_saves_once_and_shows_a_line(runtime):
    note = {"note": "The person's name is Bob and they prefer short answers."}
    rt = await runtime([
        rule(when(agent='mira', profile='decide'), answer()),
        rule(when(profile='decide'), quiet()),
        rule(when(profile='utility'), utility()),
        lambda c: FakeReply(tool_calls=[ToolCall('r1', 'remember', json.dumps(note))], delay=0)
        if c.profile == 'normal' and any(t.name == 'remember' for t in (c.tools or [])) else None,
        rule(when(profile='normal'), FakeReply('Noted, Bob.', delay=0)),
    ])
    await rt.orch.registry.load()
    await rt.post('Mira, remember my name is Bob, and keep it short.')
    await rt.idle()
    memory = rt.orch.registry.get('startup', 'mira').knowledge
    assert "## Remembered\n- The person's name is Bob and they prefer short answers." in memory
    tools = [e['data'] for e in await rt.events() if e['type'] == 'message.tool']
    assert tools and tools[0]['summary'].startswith('remembered: ')
    calls = [c for c in rt.model.calls if c.profile == 'normal']
    assert len(calls) == 2 and not any(t.name == 'remember' for t in (calls[1].tools or []))
    assert (await rt.messages())[-1]['text'] == 'Noted, Bob.'


async def test_new_agent_can_be_addressed_and_speaks(runtime):
    rt = await runtime([
        rule(when(agent='zed', profile='decide'), answer(effort='light')),
        rule(when(profile='decide'), quiet()),
        rule(when(profile='utility'), utility()),
        rule(when(agent='zed', profile='light'), FakeReply('Trucks are fine.', delay=0)),
    ])
    await rt.orch.registry.load()
    await rt.orch.registry.create('startup', {'name': 'Zed', 'role': 'Ops', 'lens': 'logistics', 'soul': SOUL})
    assert rt.orch.targets('Zed, how are the trucks?') == {'zed'}
    await rt.post('Zed, how are the trucks?')
    await rt.idle()
    assert [(m['author'], m['text']) for m in await rt.messages()][-1] == ('zed', 'Trucks are fine.')
    zed_calls = [c for c in rt.model.calls if c.agent == 'zed']
    assert zed_calls and 'Ops' in zed_calls[0].messages[0].content


async def test_reasoning_is_kept_with_tool_calls_and_sent_back(runtime):
    rt = await runtime([
        rule(when(agent='theo', profile='decide'), answer(effort='deep')),
        rule(when(profile='decide'), quiet()),
        rule(when(profile='utility'), utility()),
        lambda c: FakeReply(tool_calls=[ToolCall('t1', 'bash', '{"cmd": "ls"}')], reasoning='Check the files first.',
                            delay=0) if c.profile == 'deep' and not any(m.role == 'tool' for m in c.messages) else None,
        rule(when(profile='deep'), FakeReply('Nothing there yet.', delay=0)),
    ])
    await rt.post('Theo, what is in the workspace?')
    await rt.idle()
    second = [c for c in rt.model.calls if c.profile == 'deep'][1]
    with_calls = [m for m in second.messages if m.role == 'assistant' and m.tool_calls]
    assert with_calls and with_calls[0].reasoning == 'Check the files first.'
    steps = await rt.store.steps(rt.cid, 'theo')
    assert steps[0]['data']['reasoning'] == 'Check the files first.'


async def test_an_instruction_for_this_conversation_never_reaches_memory(runtime):
    # "Reply in Chinese from now on" belongs to this conversation; memory is shared by every conversation.
    note = {"note": "Reply in Chinese."}
    rt = await runtime([
        rule(when(agent='mira', profile='decide'), answer()),
        rule(when(profile='decide'), quiet()),
        rule(when(profile='utility'), utility()),
        lambda c: FakeReply(tool_calls=[ToolCall('r1', 'remember', json.dumps(note))], delay=0)
        if c.profile == 'normal' and any(t.name == 'remember' for t in (c.tools or [])) else None,
        rule(when(profile='normal'), FakeReply('好的。', delay=0)),
    ])
    await rt.orch.registry.load()
    await rt.post('Mira, reply in Chinese from now on.')
    await rt.idle()
    assert '## Remembered' not in rt.orch.registry.get('startup', 'mira').knowledge
    calls = [c for c in rt.model.calls if c.profile == 'normal']
    assert calls and not any(t.name == 'remember' for c in calls for t in (c.tools or []))


async def test_a_remember_call_that_was_not_offered_is_refused(runtime):
    # A deep run is offered the sandbox tools; a model that answers with remember anyway must not reach memory.
    note = {"note": "Reply in Chinese."}
    calls_seen = []

    def stray(c):
        if c.profile == 'deep' and not calls_seen:
            calls_seen.append(c)
            return FakeReply(tool_calls=[ToolCall('r1', 'remember', json.dumps(note))], delay=0)
        return None
    rt = await runtime([
        rule(when(agent='mira', profile='decide'), answer(effort='deep')),
        rule(when(profile='decide'), quiet()),
        rule(when(profile='utility'), utility()),
        stray,
        rule(when(profile='deep'), FakeReply('Done.', delay=0)),
    ])
    await rt.orch.registry.load()
    await rt.post('Mira, reply in Chinese from now on.')
    await rt.idle()
    assert calls_seen and not any(t.name == 'remember' for t in (calls_seen[0].tools or []))
    assert '## Remembered' not in rt.orch.registry.get('startup', 'mira').knowledge

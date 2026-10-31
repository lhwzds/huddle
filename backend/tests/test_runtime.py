"""Runtime integration tests: real store/event log, scripted models, no network."""
import asyncio
import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from conftest import FakeSandbox
from huddle.api import events as sse_events
from huddle.config import load_agents
from huddle.context import ContextState, build_context, estimate_tokens
from huddle.llm import ToolCall
from huddle.llm.fake import FakeLLM, FakeReply, rule, when
from huddle.orchestrator import Orchestrator
from huddle.tools import TOOL_SPECS, Sandbox, ToolSettings, run_tool


def tool_settings(**over):
    from huddle.config import load_config
    from dataclasses import replace
    return replace(ToolSettings.from_cfg(load_config()), **over)


def sandbox_tools(call):
    """Names of the sandbox tools offered in a model call (remember is left out)."""
    return [t.name for t in (call.tools or []) if t.name != 'remember']


def answer(point='new point', effort='normal', addresses=()):
    return FakeReply(json.dumps(dict(speak=True, point=point, effort=effort, addresses=list(addresses))), delay=0)


def quiet():
    return FakeReply('{"speak": false}', delay=0)


def expected_seam(partial):
    """The break the runtime puts between shown text and a steered continuation."""
    if not partial:
        return ''
    last = partial[-1]
    if last.isalnum():          # cut off mid-word or mid-sentence
        return '… '
    return '' if last.isspace() else ' '


def utility(drop=(), instructions=()):
    return FakeReply(json.dumps(dict(drop=list(drop), instructions=list(instructions))), delay=0)


def standard_rules(speakers=('mira', 'theo'), *, text='abcdefghijklmnopqrst', delay=.006):
    return [rule(when(agent=a, profile='decide'), answer(a)) for a in speakers] + [
        rule(when(profile='utility'), utility()),
        rule(lambda c: c.profile in ('light', 'normal', 'deep'), FakeReply(text, chunk=1, delay=delay)),
    ]


async def event_matching(store, cid, predicate):
    """Subscribe before replay, like the API; no polling or timing assumptions."""
    queue = store.bus.subscribe(cid)
    try:
        async with asyncio.timeout(3):
            for event in await store.events_after(cid, 0):
                if predicate(event):
                    return event
            while True:
                event = await queue.get()
                if predicate(event):
                    return event
    finally:
        store.bus.unsubscribe(cid, queue)


@dataclass
class Runtime:
    orch: Orchestrator
    model: FakeLLM
    sandbox: FakeSandbox
    store: object
    cid: str

    async def post(self, text):
        return await self.orch.on_user_message(self.cid, text)

    async def idle(self):
        async with asyncio.timeout(4):
            while self.orch._tasks:
                await asyncio.gather(*list(self.orch._tasks), return_exceptions=True)
        assert not self.orch._conversation(self.cid).runs

    async def events(self):
        return await self.store.events_after(self.cid, 0)

    async def messages(self):
        return await self.store.messages(self.cid)

    async def delta(self, agent=None):
        if agent:
            created = await event_matching(self.store, self.cid, lambda e: e['type'] == 'message.created'
                                           and e['data']['message']['author'] == agent)
            mid = created['data']['message']['id']
            return await event_matching(self.store, self.cid, lambda e: e['type'] == 'message.delta' and e['data']['id'] == mid)
        return await event_matching(self.store, self.cid, lambda e: e['type'] == 'message.delta')


async def assert_sse_matches_snapshot(rt):
    events = await rt.events()
    # Read actual SSE frames from seq 0, rebuild the visible state, compare reload.
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(store=rt.store)))
    response = await sse_events(request, rt.cid, after=0, last_event_id=None)
    stream = response.body_iterator
    rebuilt = {}
    try:
        for _ in events:
            frame = await anext(stream)
            e = json.loads(next(line[6:] for line in frame.splitlines() if line.startswith('data: ')))
            data = e['data']
            if e['type'] == 'message.created':
                rebuilt[data['message']['id']] = data['message'].copy()
            elif e['type'] == 'message.delta':
                rebuilt[data['id']]['text'] += data['text']
            elif e['type'] == 'message.done':
                rebuilt[data['id']]['status'] = data['status']
    finally:
        await stream.aclose()
    snap = await rt.store.snapshot(rt.cid, lambda: rt.orch.agents_state(rt.cid))
    assert snap['messages'] == sorted(rebuilt.values(), key=lambda m: m['pos'])
    assert snap['last_seq'] == events[-1]['seq']



@pytest.fixture
async def runtime(cfg, store):
    runtimes = []
    async def make(rules=None, *, rounds=0, cap=8, model=None, sandbox=None):
        cfg['limits']['max_agent_rounds'] = rounds
        cfg['limits']['max_agent_messages_per_turn'] = cap
        llm = model or FakeLLM(rules or [], default=quiet())
        sb = sandbox or FakeSandbox()
        orch = Orchestrator(cfg, store, lambda cfg, key: llm, sb)
        cid = (await store.create_conversation())['id']
        rt = Runtime(orch, llm, sb, store, cid)
        runtimes.append(rt)
        return rt
    yield make
    for rt in runtimes:
        await rt.orch.close()


async def test_parallel_answers_silence_and_fixed_pos(runtime):
    rt = await runtime(standard_rules())
    await rt.post('Hello everyone')
    await rt.idle()
    events = await rt.events()
    messages = await rt.messages()
    assert [m['author'] for m in messages] == ['user', 'mira', 'theo']
    created = {e['data']['message']['id']: e for e in events if e['type'] == 'message.created'}
    for m in messages:
        assert m['pos'] == created[m['id']]['seq'] == created[m['id']]['data']['message']['pos']
    writers = {m['id'] for m in messages if m['author'] != 'user'}
    delta_ids = [e['data']['id'] for e in events if e['type'] == 'message.delta']
    assert set(delta_ids) == writers
    # There are at least two transitions: not two sequential, non-overlapping answers.
    assert sum(a != b for a, b in zip(delta_ids, delta_ids[1:])) >= 2
    assert not any(e['type'] == 'agent.typing' and e['data']['agent'] == 'juno' for e in events)
    assert not any(m['author'] == 'juno' for m in messages)
    for agent in ('mira', 'theo'):
        typing = [e['data']['typing'] for e in events if e['type'] == 'agent.typing' and e['data']['agent'] == agent]
        assert typing == [True, False]
        state = rt.orch.agents_state(rt.cid)[agent]
        assert not state['typing'] and 0 < state['context']['used'] <= state['context']['limit']


@pytest.mark.parametrize('text,expected', [('Hello', ['mira']), ('Theo, hello', ['mira', 'theo'])])
async def test_dedupe_preserves_addressed_agent(runtime, text, expected):
    rules = [rule(when(profile='decide', agent=a), answer('same point')) for a in ('mira', 'theo')]
    rules += [rule(when(profile='utility'), utility(drop=['theo'])),
              rule(when(profile='normal'), FakeReply('hello', delay=0))]
    rt = await runtime(rules)
    await rt.post(text)
    await rt.idle()
    assert [m['author'] for m in await rt.messages() if m['author'] != 'user'] == expected
    assert any(c.profile == 'utility' and 'Proposed contributions:' in c.last_user for c in rt.model.calls)


async def test_steer_mid_stream_same_message_exact_partial_and_reload(runtime):
    continuation = '现在用中文回答。'
    rt = await runtime([rule(when(profile='steer'), FakeReply(continuation, delay=0))] +
                       standard_rules(('mira',), text='original ' * 80))
    await rt.post('Mira, give me an analysis')
    await rt.delta('mira')
    user = await rt.post('in Chinese please')
    await rt.idle()
    assert rt.model.cancelled == 1
    calls = [c for c in rt.model.calls if c.agent == 'mira' and c.profile in ('normal', 'steer')]
    assert [c.profile for c in calls] == ['normal', 'steer']
    steer = calls[-1]
    partial = next(m.content for m in reversed(steer.messages) if m.role == 'assistant')
    assert partial and 'in Chinese please' in steer.last_user
    messages = await rt.messages()
    mira = [m for m in messages if m['author'] == 'mira']
    assert len(mira) == 1 and mira[0]['text'] == partial + expected_seam(partial) + continuation
    assert mira[0]['pos'] < user['pos'] and mira[0]['status'] == 'done'
    events = await rt.events()
    contexts = [e for e in events if e['type'] == 'agent.context' and e['data']['agent'] == 'mira']
    deltas_before_restart = ''.join(e['data']['text'] for e in events if e['type'] == 'message.delta'
                                    and e['data']['id'] == mira[0]['id'] and e['seq'] < contexts[-1]['seq'])
    assert deltas_before_restart == partial

    await assert_sse_matches_snapshot(rt)


async def test_steer_by_name_leaves_other_writer_untouched(runtime):
    rt = await runtime([rule(when(profile='steer'), FakeReply('short.', delay=0))] +
                       standard_rules(text='long reply ' * 12, delay=.002))
    await rt.post('Hello everyone')
    await rt.delta('mira')
    await rt.delta('theo')
    await rt.post('Theo, shorter')
    await rt.idle()
    assert rt.model.cancelled == 1
    assert not any(c.profile == 'steer' and c.agent == 'mira' for c in rt.model.calls)
    assert len([c for c in rt.model.calls if c.agent == 'mira' and c.profile == 'normal']) == 1
    assert len([c for c in rt.model.calls if c.agent == 'theo' and c.profile == 'steer']) == 1
    assert len([m for m in await rt.messages() if m['author'] == 'theo']) == 1
    assert next(m for m in await rt.messages() if m['author'] == 'mira')['text'] == 'long reply ' * 12


async def test_steer_at_very_end_uses_before_done_hook(runtime):
    reached, release = asyncio.Event(), asyncio.Event()
    async def hook():
        reached.set()
        await release.wait()
    rt = await runtime([
        rule(when(profile='normal'), FakeReply('last delta', chunk=100, delay=0, before_done=hook)),
        rule(when(profile='steer'), FakeReply(' — changed now', delay=0)),
    ] + standard_rules(('mira',)))
    await rt.post('Mira, hello')
    await asyncio.wait_for(reached.wait(), 2)
    await rt.post('in Chinese please')
    release.set()
    await rt.idle()
    mira = [m for m in await rt.messages() if m['author'] == 'mira']
    # The seam adds '… ' and the continuation's own leading space is dropped.
    assert len(mira) == 1 and mira[0]['text'] == 'last delta' + '… ' + '— changed now'
    done = [e for e in await rt.events() if e['type'] == 'message.done' and e['data']['id'] == mira[0]['id']]
    assert len(done) == 1 and done[0]['data']['status'] == 'done'
    assert any(c.profile == 'steer' for c in rt.model.calls)


async def test_steer_during_tool_kills_and_records_interrupted_result(runtime):
    sandbox = FakeSandbox()
    sandbox.exec_release = asyncio.Event()
    rt = await runtime([
        rule(when(profile='deep'), FakeReply(tool_calls=[ToolCall('cmd1', 'bash', '{"cmd":"sleep 10"}')], delay=0)),
        rule(when(profile='steer'), FakeReply('No file then.', delay=0)),
    ] + [rule(when(agent='mira', profile='decide'), answer(effort='deep')),
         rule(when(profile='utility'), utility())], sandbox=sandbox)
    await rt.post('Mira, make a file')
    await asyncio.wait_for(sandbox.exec_started.wait(), 2)
    await rt.post('skip that, just explain')
    await rt.idle()
    assert sandbox.killed == [rt.cid]
    assert sandbox.exec_calls == [(rt.cid, 'sleep 10', rt.orch.cfg['limits']['bash_timeout_s'])]
    call = next(c for c in rt.model.calls if c.profile == 'steer')
    assert any(m.role == 'tool' and 'interrupted' in m.content and m.tool_call_id == 'cmd1' for m in call.messages)
    assert 'skip that, just explain' in call.last_user
    mira = next(m for m in await rt.messages() if m['author'] == 'mira')
    steps = await rt.store.steps(rt.cid, 'mira')
    assert [s['kind'] for s in steps] == ['tool_call', 'tool_result']
    assert all(s['message_id'] == mira['id'] for s in steps)
    assert any(e['type'] == 'message.tool' and e['data']['id'] == mira['id'] for e in await rt.events())
    assert mira['status'] == 'done' and mira['text'] == 'No file then.'


@pytest.mark.parametrize('command', [None, 'stop', 'Mira, stop', '@MIRA, STOP IT!!!', '够了'])
async def test_stop_endpoint_and_stop_words(runtime, command):
    rt = await runtime(standard_rules(('mira', 'theo', 'juno'), text='x' * 200, delay=.002))
    await rt.post('Hello everyone')
    for a in ('mira', 'theo', 'juno'):
        await rt.delta(a)
    if command is None:
        await rt.orch.on_stop(rt.cid)
    else:
        await rt.post(command)
    await rt.idle()
    named = command and 'MIRA' in command.upper()
    stopped = {'mira'} if named else {'mira', 'theo', 'juno'}
    assert rt.model.cancelled == len(stopped)
    messages = [m for m in await rt.messages() if m['author'] != 'user']
    assert len(messages) == 3
    for m in messages:
        assert m['status'] == ('stopped' if m['author'] in stopped else 'done')
    events = await rt.events()
    for m in messages:
        done = next(e for e in events if e['type'] == 'message.done' and e['data']['id'] == m['id'])
        assert not any(e['data'].get('id') == m['id'] for e in events if e['seq'] > done['seq'])
    assert all(not s['typing'] for s in rt.orch.agents_state(rt.cid).values())
    for a in ('mira', 'theo', 'juno'):
        assert [e['data']['typing'] for e in events if e['type'] == 'agent.typing' and e['data']['agent'] == a] == [True, False]
    # Stop is not a durable disable; the next user message starts a fresh turn.
    await rt.post('Hello again')
    await rt.idle()
    assert len([m for m in await rt.messages() if m['author'] != 'user']) == 6


@pytest.mark.parametrize('text', ['Everyone: what worries you?', 'everybody', 'all of you',
                                  'you all', "y'all", 'guys', '大家', '各位', '你们'])
async def test_group_word_addresses_every_agent(runtime, text):
    rt = await runtime(standard_rules(('mira', 'theo', 'juno')))
    await rt.post(text)
    await rt.idle()
    decisions = [c for c in rt.model.calls if c.profile == 'decide']
    assert {c.agent for c in decisions} == {'mira', 'theo', 'juno'}
    # Every agent is told it was addressed, so nobody is skipped.
    assert all('the user addressed you by name' in c.last_user for c in decisions)
    assert {m['author'] for m in await rt.messages() if m['author'] != 'user'} == {'mira', 'theo', 'juno'}


async def test_group_address_survives_dedupe(runtime):
    rules = [rule(when(profile='decide', agent=a), answer('same point')) for a in ('mira', 'theo', 'juno')]
    rules += [rule(when(profile='utility'), utility(drop=['mira', 'theo', 'juno'])),
              rule(when(profile='normal'), FakeReply('hello', delay=0))]
    rt = await runtime(rules)
    await rt.post('everyone, what worries you?')
    await rt.idle()
    assert {m['author'] for m in await rt.messages() if m['author'] != 'user'} == {'mira', 'theo', 'juno'}


async def test_group_word_steer_reaches_every_running_agent(runtime):
    rt = await runtime([rule(when(profile='steer'), FakeReply('short.', delay=0))] +
                       standard_rules(text='long reply ' * 12, delay=.002))
    await rt.post('Hello')
    await rt.delta('mira')
    await rt.delta('theo')
    await rt.post('everyone, be brief')
    await rt.idle()
    assert rt.model.cancelled == 2
    assert {c.agent for c in rt.model.calls if c.profile == 'steer'} == {'mira', 'theo'}


async def test_group_word_stop_stops_every_agent(runtime):
    rt = await runtime(standard_rules(('mira', 'theo', 'juno'), text='x' * 200, delay=.002))
    await rt.post('Hello')
    for a in ('mira', 'theo', 'juno'):
        await rt.delta(a)
    await rt.post('everyone, stop')
    await rt.idle()
    assert rt.model.cancelled == 3
    messages = [m for m in await rt.messages() if m['author'] != 'user']
    assert len(messages) == 3
    assert all(m['status'] == 'stopped' for m in messages)
    assert all(not s['typing'] for s in rt.orch.agents_state(rt.cid).values())


async def test_standing_instruction_persists_and_survives_restart(runtime):
    directive = 'Answer in Chinese'
    rt = await runtime([
        rule(when(profile='utility', contains='Chinese please'), utility(instructions=[{'agent': None, 'text': directive}])),
    ] + standard_rules(('mira', 'theo', 'juno'), text='好的', delay=0))
    first = await rt.post('Chinese please')
    await rt.idle()
    saved = await rt.store.instructions(rt.cid)
    assert [(i['agent'], i['text'], i['source_message_id']) for i in saved] == [(None, directive, first['id'])]
    # The current message is available in the transcript even before extraction.
    for call in rt.model.calls:
        if call.agent:
            assert any("Chinese please" in m.content for m in call.messages)
    before = len(rt.model.calls)
    await rt.post('Hello again')
    await rt.idle()
    assert {c.agent for c in rt.model.calls[before:] if c.agent} == {'mira', 'theo', 'juno'}
    assert all(directive in c.messages[0].content for c in rt.model.calls[before:])
    await rt.orch.close()
    restarted = Orchestrator(rt.orch.cfg, rt.store, lambda cfg, key: rt.model, rt.sandbox)
    rt.orch = restarted
    before = len(rt.model.calls)
    msg = await rt.post('A new process, same conversation')
    await rt.idle()
    assert msg['turn'] == 3
    assert all(directive in c.messages[0].content for c in rt.model.calls[before:])


async def test_agent_to_agent_round_then_silence(runtime):
    decisions = {'juno': 0, 'theo': 0}
    def scripted(call):
        if call.profile != 'decide':
            return None
        if call.agent not in decisions:
            return quiet()
        decisions[call.agent] += 1
        if call.agent == 'juno' and decisions['juno'] == 1:
            return answer('Ask Theo about rollout', addresses=['theo'])
        if call.agent == 'theo' and decisions['theo'] == 2:
            assert 'round 1' in call.last_user
            assert any('Theo, can we ship this?' in m.content for m in call.messages)
            return answer('Rollout plan', addresses=['juno'])
        return quiet()
    rt = await runtime([
        scripted,
        rule(when(agent='juno', profile='normal'), FakeReply('Theo, can we ship this?', delay=0)),
        rule(when(agent='theo', profile='normal'), FakeReply('Juno, yes, behind a flag.', delay=0)),
        rule(when(profile='utility'), utility()),
    ], rounds=3)
    await rt.post('What about the rollout?')
    await rt.idle()
    messages = await rt.messages()
    assert [m['author'] for m in messages] == ['user', 'juno', 'theo']
    assert messages[-1]['reply_to'] == ['juno']
    assert all(m['turn'] == 1 and m['status'] == 'done' for m in messages)
    assert decisions == {'juno': 1, 'theo': 2}  # Theo's reply names Juno without asking: no new round


@pytest.mark.parametrize('rounds,cap,expected', [(1, 20, 6), (8, 4, 4), (0, 2, 2)])
async def test_round_and_message_caps_bound_always_speaking_agents(runtime, rounds, cap, expected):
    rt = await runtime(standard_rules(('mira', 'theo', 'juno'), text='@Mira @Theo @Juno, a distinct fact.', delay=0), rounds=rounds, cap=cap)
    await rt.post('Thoughts?')
    await rt.idle()
    agents = [m for m in await rt.messages() if m['author'] != 'user']
    assert len(agents) == expected
    assert all(m['status'] == 'done' for m in agents)
    assert len([e for e in await rt.events() if e['type'] == 'message.done']) == expected


async def test_new_user_supersedes_in_progress_round_decision(runtime):
    entered, never = asyncio.Event(), asyncio.Event()
    async def deciding():
        entered.set()
        await never.wait()
    def script(call):
        if call.profile != 'decide':
            return None
        if call.agent == 'juno' and 'New topic' not in ' '.join(m.content for m in call.messages):
            return answer('ask Mira', addresses=['mira'])
        if call.agent != 'mira':
            return quiet()
        if 'round 1' in call.last_user:
            return FakeReply('{"speak": true}', chunk=100, delay=0, before_done=deciding)
        if 'New topic' not in ' '.join(m.content for m in call.messages):
            return quiet()
        return answer('new reply')
    rt = await runtime([script,
        rule(when(profile='utility'), utility()),
        rule(when(agent='juno', profile='normal'), FakeReply('Mira, can you check?', delay=0)),
        rule(when(profile='normal'), FakeReply('reply', delay=0)),
    ], rounds=2)
    await rt.post('First request')
    await asyncio.wait_for(entered.wait(), 2)
    old_driver = rt.orch._conversation(rt.cid).turn_task
    # Do not let the new turn itself enter the blocking round-1 rule.
    rt.orch.cfg['limits']['max_agent_rounds'] = 0
    second = await rt.post('New topic')
    await rt.idle()
    assert old_driver.cancelled()
    assert rt.model.cancelled == 1
    messages = await rt.messages()
    assert [(m['author'], m['turn']) for m in messages] == [('user', 1), ('juno', 1), ('user', 2), ('mira', 2)]
    assert second['id'] == messages[2]['id'] and all(m['status'] == 'done' for m in messages)


async def test_burst_stores_every_user_once_in_arrival_order_and_finishes(runtime):
    rt = await runtime([rule(when(profile='steer'), FakeReply('continued', chunk=1, delay=.001))] +
                       standard_rules(('mira', 'theo', 'juno'), text='lengthy ' * 30, delay=.001))
    await rt.post('Begin')
    await rt.delta('mira')
    # One batch, not a loop of artificial sleeps; all arrive well within 200 ms.
    texts = [f'additional detail {i}' for i in range(20)]
    replies = await asyncio.gather(*(rt.post(text) for text in texts))
    await rt.idle()
    messages = await rt.messages()
    users = [m for m in messages if m['author'] == 'user']
    assert [m['text'] for m in users] == ['Begin', *texts]
    assert len({m['id'] for m in replies}) == 20
    assert [m['turn'] for m in users] == list(range(1, 22))
    assert [m['pos'] for m in users] == sorted(m['pos'] for m in users)
    assert all(m['status'] == 'done' for m in messages)
    events = await rt.events()
    assert [e['seq'] for e in events] == list(range(1, len(events) + 1))
    assert len([e for e in events if e['type'] == 'message.created' and e['data']['message']['author'] == 'user']) == 21
    assert not rt.orch._conversation(rt.cid).runs


async def test_light_effort_has_no_tools_and_correct_token_cap(runtime):
    rt = await runtime([
        rule(when(agent='mira', profile='decide'), answer(effort='light')),
        rule(when(profile='utility'), utility()),
        rule(when(profile='light'), FakeReply('Hi.', delay=0)),
    ])
    await rt.post('Mira, hello')
    await rt.idle()
    call = next(c for c in rt.model.calls if c.profile == 'light')
    assert sandbox_tools(call) == [] and call.max_tokens == rt.orch.cfg['limits']['max_tokens']['light']
    assert 'One or two short sentences' in call.messages[0].content
    assert next(m for m in await rt.messages() if m['author'] == 'mira')['effort'] == 'light'


async def test_private_knowledge_and_tool_history_never_cross_agents(runtime):
    rt = await runtime(standard_rules(('mira', 'theo', 'juno'), text='Public reply.', delay=0))
    # Unique sentinels, not substrings that happen to occur in multiple personas.
    for agent in rt.orch.agents:
        agent.knowledge = 'PRIVATE_' + agent.key + '_ONLY'
    previous = await rt.store.add_message(rt.cid, author='mira', turn=0, text='public past', status='done')
    await rt.store.add_step(rt.cid, 'mira', previous['id'], 'tool_call',
                            {'id': 'secret', 'name': 'bash', 'arguments': '{"cmd":"PRIVATE_TOOL"}'})
    await rt.store.add_step(rt.cid, 'mira', previous['id'], 'tool_result',
                            {'tool_call_id': 'secret', 'content': 'PRIVATE_RESULT'})
    await rt.post('Hello everyone')
    await rt.idle()
    for c in rt.model.calls:
        if c.agent:
            texts = '\n'.join(m.content + ''.join(tc.arguments for tc in m.tool_calls or []) for m in c.messages)
            assert 'PRIVATE_' + c.agent + '_ONLY' in texts
            for other in {'mira', 'theo', 'juno'} - {c.agent}:
                assert 'PRIVATE_' + other + '_ONLY' not in texts
            if c.agent != 'mira':
                assert 'PRIVATE_TOOL' not in texts and 'PRIVATE_RESULT' not in texts
    assert any(m.role == 'tool' and m.content == 'PRIVATE_RESULT'
               for c in rt.model.calls if c.agent == 'mira' for m in c.messages)


def test_context_transcript_order_own_partial_steps_and_instruction_scope(cfg):
    roster = load_agents(cfg)
    mira, theo, juno = roster
    def msg(mid, author, text, pos, status='done'):
        return {'id': mid, 'author': author, 'text': text, 'pos': pos, 'status': status}
    transcript = [msg('u2', 'user', 'second', 4), msg('m1', 'mira', 'old reply', 2),
                  msg('u1', 'user', 'first', 1), msg('t1', 'theo', 'working', 3, 'streaming'),
                  msg('current', 'mira', 'exact partial text ', 5, 'streaming'), msg('u3', 'user', 'new instruction', 6)]
    steps = [
        {'agent': 'mira', 'message_id': 'current', 'kind': 'tool_call',
         'data': {'id': 'tc', 'name': 'read_file', 'arguments': '{"path":"a"}'}},
        {'agent': 'mira', 'message_id': 'current', 'kind': 'tool_result',
         'data': {'tool_call_id': 'tc', 'content': 'own tool result'}},
        {'agent': 'theo', 'message_id': 't1', 'kind': 'tool_result',
         'data': {'tool_call_id': 'secret', 'content': 'THEO_PRIVATE_RESULT'}},
    ]
    instructions = [{'agent': None, 'text': 'First directive'}, {'agent': 'theo', 'text': 'THEO_ONLY'},
                    {'agent': 'mira', 'text': 'Third directive'}]
    state = ContextState(roster, transcript, instructions, steps, cfg['limits'])
    built = build_context(mira, state, purpose='speak', own_message_id='current', steer=['be concise', 'use Chinese'])
    assert built.messages[0].role == 'system'
    system = built.messages[0].content
    assert system.index('First directive') < system.index('Third directive')
    assert 'THEO_ONLY' not in system and theo.knowledge not in system and juno.knowledge not in system
    assert mira.knowledge in system
    assert any(m.content == 'Theo (CTO): working …(still typing)\n\nUser: second\n\nUser: new instruction'
               for m in built.messages)
    assert built.messages[-2].role == 'assistant' and built.messages[-2].content == 'exact partial text '
    assert built.messages[-3].role == 'tool' and built.messages[-3].tool_call_id == 'tc'
    assert built.messages[-4].tool_calls[0].id == 'tc'
    assert built.messages[-1].role == 'user'
    assert 'be concise' in built.messages[-1].content and 'use Chinese' in built.messages[-1].content
    assert all('THEO_PRIVATE_RESULT' not in m.content for m in built.messages)
    assert all(a.role != b.role or a.role != 'user' for a, b in zip(built.messages, built.messages[1:]))


def test_context_tokens_trims_oldest_blocks_but_protects_system_partial_steer(cfg):
    assert estimate_tokens('abcde中文') == 4  # ceil(5 / 4) + 2
    assert estimate_tokens('') == 0 and estimate_tokens('中文') == 2
    roster = load_agents(cfg)
    mira = roster[0]
    messages = [{'id': str(i), 'author': 'user', 'text': f'old-{i} ' + 'x' * 1000, 'pos': i, 'status': 'done'}
                for i in range(6)]
    messages.append({'id': 'partial', 'author': 'mira', 'text': 'KEEP_PARTIAL', 'pos': 7, 'status': 'streaming'})
    limits = {**cfg['limits'], 'context_tokens': 1}
    state = ContextState(roster, messages, [], [], limits)
    built = build_context(mira, state, purpose='speak', own_message_id='partial', steer=['KEEP_STEER'])
    assert [m.role for m in built.messages] == ['system', 'assistant', 'user']
    assert built.messages[1].content == 'KEEP_PARTIAL' and 'KEEP_STEER' in built.messages[-1].content
    assert built.used > built.limit == 1
    assert all('old-' not in m.content for m in built.messages)
    # With room for one more block, the newest transcript entry is retained.
    limits['context_tokens'] = built.used + 260
    built = build_context(mira, state, purpose='speak', own_message_id='partial', steer=['KEEP_STEER'])
    assert any('old-5' in m.content for m in built.messages)
    assert not any('old-4' in m.content for m in built.messages)
    assert built.used <= built.limit


def test_context_only_includes_tool_history_of_three_most_recent_own_messages(cfg):
    roster = load_agents(cfg)
    messages, steps = [], []
    for i in range(5):
        messages.append({'id': str(i), 'author': 'mira', 'text': f'reply {i}', 'pos': i, 'status': 'done'})
        for kind, data in [('tool_call', {'id': f'c{i}', 'name': 'bash', 'arguments': '{}'}),
                           ('tool_result', {'tool_call_id': f'c{i}', 'content': f'result {i}'})]:
            steps.append({'agent': 'mira', 'message_id': str(i), 'kind': kind, 'data': data})
    built = build_context(roster[0], ContextState(roster, messages, [], steps, cfg['limits']), purpose='decide')
    assert [m.content for m in built.messages if m.role == 'tool'] == ['result 2', 'result 3', 'result 4']
    assert all(any(m.content == f'reply {i}' for m in built.messages) for i in range(5))


async def test_tools_dispatch_capped_errors_summaries_and_sandbox_contract():
    import httpx
    sb = FakeSandbox()
    output, summary = await run_tool(sb, 'workspace', ToolCall('w', 'write_file', json.dumps({'path': 'notes.txt', 'content': 'x' * 10000})), tool_settings())
    assert summary == 'wrote notes.txt' and json.loads(output)['size'] == 10000
    output, summary = await run_tool(sb, 'workspace', ToolCall('r', 'read_file', '{"path":"notes.txt"}'), tool_settings())
    assert len(output) == 6000 and summary == 'read notes.txt'
    output, summary = await run_tool(sb, 'workspace', ToolCall('b', 'bash', json.dumps({'cmd': 'x' * 100})), tool_settings(bash_timeout_s=7))
    assert summary == 'ran `' + 'x' * 60 + '`' and sb.exec_calls[-1] == ('workspace', 'x' * 100, 7)
    for call in [ToolCall('x', 'unknown', '{}'), ToolCall('x', 'bash', '{bad json'),
                 ToolCall('x', 'bash', '[]'), ToolCall('x', 'read_file', '{"path":42}')]:
        result, _ = await run_tool(sb, 'workspace', call, tool_settings())
        assert result.startswith('error:')
    assert [s.name for s in TOOL_SPECS] == ['bash', 'write_file', 'read_file', 'web_search', 'fetch_url']
    assert all(s.parameters['type'] == 'object' and s.parameters['required'] for s in TOOL_SPECS)

    requests = []
    def handler(request):
        requests.append(request)
        path = request.url.path
        if path == '/kill':
            return httpx.Response(200, json={'killed': 2})
        if path == '/files':
            return httpx.Response(200, json=[{'path': 'a', 'size': 1, 'mtime': 'now'}])
        if path == '/file' and request.method == 'GET':
            return httpx.Response(200, json={'path': 'a', 'content': 'x', 'binary': False})
        if path == '/file':
            return httpx.Response(200, json={'path': 'a', 'size': 1})
        return httpx.Response(200, json={'exit_code': 0, 'stdout': 'ok', 'stderr': '', 'timed_out': False, 'truncated': False})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url='http://sandbox') as client:
        sandbox = Sandbox('http://sandbox', client=client)
        assert (await sandbox.exec('conv', 'ls', 3))['stdout'] == 'ok'
        assert await sandbox.kill('conv') == 2
        assert (await sandbox.files('conv'))[0]['path'] == 'a'
        assert (await sandbox.read('conv', 'a'))['content'] == 'x'
        assert (await sandbox.write('conv', 'a', 'x'))['size'] == 1
    assert json.loads(requests[0].content) == {'workspace': 'conv', 'cmd': 'ls', 'timeout_s': 3}
    assert json.loads(requests[1].content) == {'workspace': 'conv'}
    assert dict(requests[2].url.params) == {'workspace': 'conv'}
    assert dict(requests[3].url.params) == {'workspace': 'conv', 'path': 'a'}
    assert json.loads(requests[4].content) == {'workspace': 'conv', 'path': 'a', 'content': 'x'}


async def test_tool_file_events_and_step_cap(runtime):
    rt = await runtime([
        rule(when(agent='mira', profile='decide'), answer()),
        rule(when(profile='utility'), utility()),
        lambda c: FakeReply(tool_calls=[ToolCall('write', 'write_file', '{"path":"plan.txt","content":"plan"}')], delay=0)
        if c.profile == 'normal' and sandbox_tools(c) else None,
        rule(when(profile='normal'), FakeReply('Created plan.txt', delay=0)),
    ])
    rt.orch.cfg['limits']['max_tool_steps']['normal'] = 1
    await rt.post('Mira, sort this out')
    await rt.idle()
    calls = [c for c in rt.model.calls if c.profile == 'normal']
    # Budget 1: the first call runs; later calls still see the tools (withdrawing them mid-run makes some
    # models print raw tool markup) but get "budget used up"; two steps past the budget the tools go.
    assert calls[0].tools == TOOL_SPECS and sandbox_tools(calls[-1]) == []
    assert len(calls) == 4 and rt.sandbox.write_calls == 1 if hasattr(rt.sandbox, 'write_calls') else len(calls) == 4
    assert any(m.role == 'tool' and 'plan.txt' in m.content for m in calls[1].messages)
    assert any(m.role == 'tool' and 'budget used up' in m.content for m in calls[-1].messages)
    changed = [e for e in await rt.events() if e['type'] == 'file.changed']
    assert [e['data'] for e in changed] == [{'path': 'plan.txt', 'size': 4, 'agent': 'mira'}]
    mira = next(m for m in await rt.messages() if m['author'] == 'mira')
    assert mira['text'] == 'Created plan.txt' and mira['status'] == 'done'


async def test_simultaneously_ready_delta_and_inbox_never_loses_delta(runtime):
    rt = None
    source = None
    def both_ready(call):
        if call.profile == 'normal':
            # This executes inside anext's task before it yields its first delta.
            # Queue.get and anext are therefore both ready when wait resumes.
            run = rt.orch._conversation(rt.cid).runs['mira']
            assert run.steer(source['id'])
            return FakeReply('RECEIVED_DELTA', chunk=100, delay=0)
    rt = await runtime([both_ready, rule(when(profile='steer'), FakeReply('续写', delay=0))] + standard_rules(('mira',)))
    source = await rt.post('Mira, in Chinese please')
    await rt.idle()
    call = next(c for c in rt.model.calls if c.profile == 'steer')
    assert next(m.content for m in reversed(call.messages) if m.role == 'assistant') == 'RECEIVED_DELTA'
    assert next(m for m in await rt.messages() if m['author'] == 'mira')['text'] == 'RECEIVED_DELTA… 续写'
    assert rt.model.cancelled == 1


async def test_several_steers_coalesce_before_next_context(runtime):
    rt = await runtime([rule(when(profile='steer'), FakeReply('continuation', delay=0))] +
                       standard_rules(('mira',), text='long reply ' * 100))
    await rt.post('Mira, analyze this')
    await rt.delta('mira')
    async with rt.orch._conversation(rt.cid).lock:
        for text in ('use Chinese', 'one sentence', 'focus on price'):
            await rt.post(text)
    await rt.idle()
    calls = [c for c in rt.model.calls if c.profile == 'steer']
    assert len(calls) == 1
    assert all(text in calls[0].last_user for text in ('use Chinese', 'one sentence', 'focus on price'))
    assert len([m for m in await rt.messages() if m['author'] == 'mira']) == 1


async def test_user_waiting_on_finalization_lock_gets_fresh_run_not_lost(runtime, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    rt = await runtime(standard_rules(('mira',), text='finished', delay=0))
    original = rt.store.finish_message
    first = True
    async def gated(mid, status):
        nonlocal first
        if first:
            first = False
            entered.set()
            await release.wait()
        return await original(mid, status)
    monkeypatch.setattr(rt.store, 'finish_message', gated)
    await rt.post('Mira, first question')
    await asyncio.wait_for(entered.wait(), 2)
    old = rt.orch._conversation(rt.cid).runs['mira']
    assert not old.steer('not-a-real-message')  # final inbox check has already linearized
    second = asyncio.create_task(rt.post('Mira, next question'))
    release.set()
    await second
    await rt.idle()
    messages = await rt.messages()
    assert [(m['author'], m['turn']) for m in messages] == [('user', 1), ('mira', 1), ('user', 2), ('mira', 2)]
    assert all(m['status'] == 'done' for m in messages)
    assert not any(c.profile == 'steer' for c in rt.model.calls)


@pytest.mark.parametrize('text,bad_decision,expected_profile', [
    ('A statement', False, 'light'), ('What should we do?', False, 'light'),
    ('@Mira, help', True, 'normal'), ('Mira, a statement', False, 'light'),
])
async def test_silence_question_fallback_and_invalid_named_decision(runtime, text, bad_decision, expected_profile):
    rt = await runtime([
        rule(when(profile='utility'), utility()),
        rule(when(profile='decide'), FakeReply('invalid JSON', delay=0) if bad_decision else quiet()),
        rule(lambda c: c.profile in ('light', 'normal'), FakeReply('an answer', delay=0)),
    ])
    await rt.post(text)
    await rt.idle()
    actual = [c.profile for c in rt.model.calls if c.profile in ('light', 'normal', 'deep')]
    assert actual == ([expected_profile] if expected_profile else [])
    assert len([m for m in await rt.messages() if m['author'] != 'user']) == int(expected_profile is not None)


async def test_decide_timeout_falls_back_only_for_named_agent(runtime):
    rt = await runtime([
        rule(when(profile='utility'), utility()),
        rule(when(profile='decide'), FakeReply('{"speak":true}', first_delay=1)),
        rule(when(profile='normal'), FakeReply('brief answer', delay=0)),
    ])
    rt.orch.cfg['limits']['decide_timeout_s'] = .01
    await rt.post('Theo, a question')
    await rt.idle()
    assert [m['author'] for m in await rt.messages()] == ['user', 'theo']
    # No count of cancelled or recorded decide streams: with a 10 ms timeout a stream can be
    # cancelled before it starts, which made those counts flaky. The behaviour above is the point.


@pytest.mark.parametrize('drop', [['mira', 'theo'], 'invalid'])
async def test_dedupe_never_drops_every_speaker_and_bad_drop_is_ignored(runtime, drop):
    rt = await runtime([
        rule(when(profile='utility'), FakeReply(json.dumps({'drop': drop, 'instructions': []}), delay=0)),
    ] + standard_rules())
    await rt.post('Hello')
    await rt.idle()
    assert [m['author'] for m in await rt.messages() if m['author'] != 'user'] == (['mira'] if isinstance(drop, list) else ['mira', 'theo'])


async def test_empty_reply_never_creates_empty_message(runtime):
    rt = await runtime([
        rule(when(agent='mira', profile='decide'), answer()),
        rule(when(profile='utility'), utility()),
        rule(when(profile='normal'), FakeReply('', delay=0)),
    ])
    await rt.post('Mira, hello')
    await rt.idle()
    assert [m['author'] for m in await rt.messages()] == ['user']
    assert not rt.orch.agents_state(rt.cid)['mira']['typing']


async def test_stop_during_tool_kills_without_post_done_events(runtime):
    sb = FakeSandbox()
    sb.exec_release = asyncio.Event()
    rt = await runtime([
        rule(when(profile='deep'), FakeReply(tool_calls=[ToolCall('cmd', 'bash', '{"cmd":"blocked"}')], delay=0)),
    ] + [rule(when(agent='mira', profile='decide'), answer(effort='deep')),
         rule(when(profile='utility'), utility())], sandbox=sb)
    await rt.post('Mira, run a command')
    await asyncio.wait_for(sb.exec_started.wait(), 2)
    await rt.orch.on_stop(rt.cid)
    await rt.idle()
    assert sb.killed == [rt.cid]
    assert all(m['author'] != 'mira' for m in await rt.messages())
    assert not any(e['type'] == 'message.tool' for e in await rt.events())


@pytest.mark.parametrize('partial', ['', 'already visible'])
async def test_model_error_says_sorry_but_never_exposes_the_error(runtime, partial):
    async def fail():
        raise RuntimeError('deliberate error for test')
    rt = await runtime([
        rule(when(profile='normal'), FakeReply(partial, chunk=100, delay=0, before_done=fail)),
    ] + standard_rules(('mira',)))
    await rt.post('Mira, hello')
    await rt.idle()
    from huddle import prompts
    messages = [m for m in await rt.messages() if m['author'] == 'mira']
    assert len(messages) == 1 and messages[0]['status'] == 'done'
    assert messages[0]['text'] == (partial + '… ' if partial else '') + prompts.RUN_FAILED
    assert 'deliberate error' not in json.dumps(await rt.events())
    assert not rt.orch.agents_state(rt.cid)['mira']['typing']


async def test_stop_before_first_text_closes_stream_and_clears_typing(runtime):
    rt = await runtime([
        rule(when(profile='normal'), FakeReply('not yet visible', first_delay=10)),
    ] + standard_rules(('mira',)))
    await rt.post('Mira, hello')
    await event_matching(rt.store, rt.cid, lambda e: e['type'] == 'agent.context')
    await rt.orch.on_stop(rt.cid)
    await rt.idle()
    assert [m['author'] for m in await rt.messages()] == ['user']
    assert not rt.orch.agents_state(rt.cid)['mira']['typing']
    assert not any(e['type'] == 'message.done' for e in await rt.events())


def test_name_targets_and_hard_stop_matching(cfg, store):
    orch = Orchestrator(cfg, store, lambda cfg, key: FakeLLM(), FakeSandbox())
    assert orch.targets('@MIRA and theo') == {'mira', 'theo'}
    assert orch.targets('admiral theorem junos') == set()
    # A word for the group addresses every agent; a name still addresses one.
    assert orch.targets('Mira and Theo') == {'mira', 'theo'}
    for text in ('Everyone', 'everybody', 'all of you', 'each of you', 'What do you each think', 'both of you', 'you all', "y'all", 'guys',
                 '大家', '各位', '你们'):
        assert orch.targets(text) == {'mira', 'theo', 'juno'}, text
    for text in ('everyone stop', 'everybody, stop', "y'all, stop it", 'guys: enough', '大家，停下'):
        assert orch._hard_stop(text), text
    for text in ('stop', '/stop', 'Mira, stop!', '@Theo, stop it.', 'Juno：别说了！', 'Mira and Theo stop'):
        # "and" is content, not punctuation or a name; it must not silently stop.
        assert orch._hard_stop(text) == (text != 'Mira and Theo stop')
    for text in ('stop loss analysis', 'Theo, shorter', 'please stop', 'stop for a moment'):
        assert not orch._hard_stop(text)


async def test_multi_agent_reload_after_named_steer_matches_sse_replay(runtime):
    rt = await runtime([rule(when(profile='steer'), FakeReply('中文续写', delay=0))] + standard_rules())
    await rt.post('Hello everyone')
    await rt.delta('mira')
    await rt.delta('theo')
    await rt.post('Mira, use Chinese')
    await rt.idle()
    assert rt.model.cancelled == 1
    agents = [m for m in await rt.messages() if m['author'] != 'user']
    assert {m['author'] for m in agents} == {'mira', 'theo'}
    assert all(m['status'] == 'done' for m in agents)
    await assert_sse_matches_snapshot(rt)


async def test_active_runs_stop_through_http_endpoint(app_client):
    client = app_client
    app = client._transport.app
    model = app.state.orchestrator._llm()
    model.rules = standard_rules(('mira', 'theo', 'juno'), text='long reply ' * 100)
    app.state.cfg['limits']['max_agent_rounds'] = 0
    cid = (await client.post('/api/conversations', json={})).json()['id']
    await client.post(f'/api/conversations/{cid}/messages', json={'text': 'Hello everyone'})
    for agent in ('mira', 'theo', 'juno'):
        await event_matching(app.state.store, cid, lambda e: e['type'] == 'message.created'
                             and e['data']['message']['author'] == agent)
    response = await client.post(f'/api/conversations/{cid}/stop')
    assert response.status_code == 202 and response.json() == {}
    snapshot = (await client.get(f'/api/conversations/{cid}')).json()
    assert len(snapshot['messages']) == 4
    assert all(m['status'] == 'stopped' for m in snapshot['messages'] if m['author'] != 'user')
    assert all(not a['typing'] for a in snapshot['agents'])
    assert model.cancelled == 3

async def test_truncated_decision_speaks_with_light_effort(runtime):
    rt = await runtime([
        rule(when(agent='mira', profile='decide'),
             FakeReply('{"speak": true, "effort": "light", "addresses": [], "point": "Cash is $2.5', delay=.01)),
        rule(when(profile='utility'), utility()),
        rule(when(agent='mira', profile='light'), FakeReply('Cash is $2.5 million.', delay=.01)),
    ])
    await rt.post('Tell me about finances')
    await rt.idle()
    messages = [m for m in await rt.messages() if m['author'] == 'mira']
    assert len(messages) == 1
    assert messages[0]['effort'] == 'light'
    assert messages[0]['text'] == 'Cash is $2.5 million.'


async def test_current_extractor_does_not_delay_first_delta(runtime):
    rt = await runtime([
        rule(when(profile='utility', contains='Extract durable'), FakeReply('{"instructions": []}', first_delay=3, delay=.01)),
        rule(when(agent='mira', profile='decide'), FakeReply('{"speak": true}', delay=.01)),
        rule(when(agent='mira', profile='normal'), FakeReply('Fast reply', delay=.01)),
    ])
    start = asyncio.get_running_loop().time()
    await rt.post('Hello')
    await rt.delta('mira')
    assert asyncio.get_running_loop().time() - start < 1
    await rt.idle()


async def test_held_dedupe_drops_silently_and_flushes_complete_reply(runtime):
    returned = asyncio.Event()
    async def mark_returned():
        returned.set()
    reply = 'The entire reply from Mira.'
    rt = await runtime([
        rule(when(profile='utility', contains='Remove redundant'),
             FakeReply('{"drop": ["theo"]}', first_delay=.3, delay=0, before_done=mark_returned)),
        rule(when(profile='utility'), utility()),
        rule(when(agent='mira', profile='decide'), answer()),
        rule(when(agent='theo', profile='decide'), answer()),
        rule(when(profile='normal'), FakeReply(reply, chunk=1, delay=.01)),
    ])
    await rt.post('Hello')
    await asyncio.sleep(.1)
    assert not returned.is_set()
    assert not any(e['type'] == 'message.delta' for e in await rt.events())
    delta = await rt.delta('mira')
    assert returned.is_set()
    await rt.idle()
    events = await rt.events()
    assert not any(e['data'].get('agent') == 'theo' or
                   (e['type'] == 'message.created' and e['data']['message']['author'] == 'theo')
                   for e in events)
    assert next(m for m in await rt.messages() if m['author'] == 'mira')['text'] == reply
    assert delta['type'] == 'message.delta'


async def test_steer_during_hold_continues_kept_partial(runtime):
    started = asyncio.Event()
    async def mark_started():
        started.set()
    rt = await runtime([
        rule(when(profile='utility', contains='Remove redundant'),
             FakeReply('{"drop": ["theo"]}', first_delay=.3, delay=0)),
        rule(when(profile='utility'), utility()),
        rule(when(agent='mira', profile='decide'), answer()),
        rule(when(agent='theo', profile='decide'), answer()),
        rule(when(profile='normal'), FakeReply('Original partial.', delay=.01, before_done=mark_started)),
        rule(when(profile='steer'), FakeReply(' Continued in Chinese.', delay=.01)),
    ])
    await rt.post('Hello')
    await asyncio.wait_for(started.wait(), 1)
    await rt.post('Mira, use Chinese')
    await rt.idle()
    mira = [m for m in await rt.messages() if m['author'] == 'mira']
    assert len(mira) == 1
    assert mira[0]['text'] == 'Original partial. ' + ' Continued in Chinese.'
    steer = next(c for c in rt.model.calls if c.agent == 'mira' and c.profile == 'steer')
    assert any(m.role == 'assistant' and m.content == 'Original partial.' for m in steer.messages)
    assert 'use Chinese' in steer.last_user


@pytest.mark.parametrize('decision,effort', [
    ('{"speak": true}', 'normal'),
    ('{"speak": true, "effort": [], "addresses": "bad", "point": 1}', 'normal'),
])
async def test_decision_optional_fields_default(runtime, decision, effort):
    rt = await runtime([
        rule(when(agent='mira', profile='decide'), FakeReply(decision, delay=0)),
        rule(when(profile='utility'), utility()),
        rule(when(profile='normal'), FakeReply('Answer', delay=0)),
    ])
    await rt.post('Hi')
    await rt.idle()
    message = next(m for m in await rt.messages() if m['author'] == 'mira')
    assert message['effort'] == effort
    assert message['reply_to'] == []


async def test_next_turn_waits_for_prior_extractor(runtime):
    extracted = asyncio.Event()
    async def mark_extracted():
        extracted.set()
    def check_decide(call):
        if call.profile == 'decide' and any('Second request' in m.content for m in call.messages):
            assert extracted.is_set()
            assert 'Answer in Chinese' in call.messages[0].content
        return None
    rt = await runtime([
        check_decide,
        rule(when(profile='utility', contains='First request'),
             FakeReply('{"instructions": [{"agent": null, "text": "Answer in Chinese"}]}',
                       first_delay=.3, delay=0, before_done=mark_extracted)),
        rule(when(profile='utility'), utility()),
    ])
    await rt.post('First request')
    await rt.post('Second request')
    await rt.idle()
    assert extracted.is_set()
    assert any(c.profile == 'decide' and any('Second request' in m.content for m in c.messages)
               for c in rt.model.calls)


async def test_held_tool_calls_do_not_execute_until_dedupe(runtime):
    dedupe_returned = asyncio.Event()
    async def returned():
        dedupe_returned.set()
    sandbox = FakeSandbox()
    original_exec = sandbox.exec
    async def checked_exec(*args):
        assert dedupe_returned.is_set()
        return await original_exec(*args)
    sandbox.exec = checked_exec
    def speak(call):
        if call.profile == 'deep':
            if any(m.role == 'tool' for m in call.messages):
                return FakeReply('Done', delay=0)
            return FakeReply(tool_calls=[ToolCall(call.agent, 'bash', '{"cmd":"echo hi"}')], delay=0)
        return None
    rt = await runtime([
        rule(when(profile='utility', contains='Remove redundant'),
             FakeReply('{"drop": ["theo"]}', first_delay=.3, delay=0, before_done=returned)),
        rule(when(profile='utility'), utility()),
        rule(when(agent='mira', profile='decide'), answer(effort='deep')),
        rule(when(agent='theo', profile='decide'), answer(effort='deep')),
        speak,
    ], sandbox=sandbox)
    rt.orch.cfg['limits']['max_discussion_rounds'] = 0  # two deep speakers would start a discussion
    await rt.post('Please write notes.md')
    await asyncio.sleep(.1)
    assert not sandbox.exec_calls
    await rt.idle()
    assert len(sandbox.exec_calls) == 1
    assert not any(e['data'].get('agent') == 'theo' for e in await rt.events())


async def test_json_repair_and_error_excerpt():
    from huddle.llm import complete_json
    for raw, expected in [
        ('prefix {"speak": true, "point": "A { brace', {'speak': True, 'point': 'A { brace'}),
        ('{"speak": true, "addresses": ["mira"', {'speak': True, 'addresses': ['mira']}),
        ('{"point": "a \\"quote\\" and slash\\', {'point': 'a "quote" and slash\\'}),
    ]:
        assert await complete_json(FakeLLM(default=FakeReply(raw, delay=0)), [], profile='decide', max_tokens=500) == expected
    raw = 'x' * 300 + 'SECRET'
    with pytest.raises(ValueError) as exc:
        await complete_json(FakeLLM(default=FakeReply(raw, delay=0)), [], profile='decide', max_tokens=500)
    assert raw[:300] in str(exc.value)
    assert 'SECRET' not in str(exc.value)


async def test_greeting_fallback_point_address_and_continuity(runtime):
    # Nobody has spoken yet -> roster order; afterwards an unnamed message continues with the last speaker.
    rules = [rule(when(profile='utility'), utility()),
             rule(lambda c: c.profile in ('light', 'normal'), FakeReply('hi', delay=0))]
    rt = await runtime(rules)
    await rt.post('morning all')
    await rt.idle()
    await rt.post('morning all')
    await rt.idle()
    await rt.post('Juno, morning')
    await rt.idle()
    await rt.post('morning all')
    await rt.idle()
    assert [(m['author'], m['effort']) for m in await rt.messages() if m['author'] != 'user'] == [
        ('mira', 'light'), ('mira', 'light'), ('juno', 'light'), ('juno', 'light')]

    rt2 = await runtime([rule(when(agent='theo', profile='decide'),
                              FakeReply('{"speak": false, "point": "relevant insight"}', delay=0))] + rules)
    await rt2.post('morning all')
    await rt2.idle()
    assert [(m['author'], m['effort']) for m in await rt2.messages() if m['author'] != 'user'] == [('theo', 'light')]
    await rt2.post('Juno, morning')
    await rt2.idle()
    assert [m['author'] for m in await rt2.messages() if m['author'] != 'user'] == ['theo', 'juno']


@pytest.mark.parametrize('steer', ['Mira, shorter', 'shorter please'])
async def test_pure_steer_has_no_fallback(runtime, steer):
    reached, release = asyncio.Event(), asyncio.Event()
    async def wait():
        reached.set()
        await release.wait()
    rt = await runtime([
        rule(when(agent='mira', profile='decide'), answer()),
        rule(when(profile='utility'), utility()),
        rule(when(profile='normal'), FakeReply('first', delay=0, before_done=wait)),
        rule(when(profile='steer'), FakeReply('continued', delay=0)),
    ])
    await rt.post('Mira, explain')
    await asyncio.wait_for(reached.wait(), 2)
    await rt.post(steer)
    release.set()
    await rt.idle()
    assert [m['author'] for m in await rt.messages() if m['author'] != 'user'] == ['mira']


@pytest.mark.parametrize('reply_to,text,recipient', [
    (['juno'], 'A fact.', None),  # decide's addresses alone no longer open a round
    ([], 'Theo?', 'theo'),
    ([], 'Theo,\ncan you help?', 'theo'),
    ([], '@Theo take this.', 'theo'),
])
async def test_only_addressed_agents_get_next_round_decisions(runtime, reply_to, text, recipient):
    rt = await runtime([
        rule(when(agent='mira', profile='decide'), answer('fact', addresses=reply_to)),
        rule(when(profile='decide'), quiet()),
        rule(when(profile='utility'), utility()),
        rule(when(profile='normal'), FakeReply(text, delay=0)),
    ], rounds=2)
    await rt.post('What is the situation?')
    await rt.idle()
    round1 = [c.agent for c in rt.model.calls if c.profile == 'decide' and 'round 1' in c.last_user]
    assert round1 == ([recipient] if recipient else [])
    assert not any('round 2' in c.last_user for c in rt.model.calls if c.profile == 'decide')


async def test_round_limit_and_no_self_address(runtime):
    rt = await runtime(standard_rules(('mira', 'theo', 'juno'), text='@Mira @Theo @Juno', delay=0), rounds=2, cap=20)
    await rt.post('Thoughts?')
    await rt.idle()
    assert len([m for m in await rt.messages() if m['author'] != 'user']) == 9
    assert not any('round 3' in c.last_user for c in rt.model.calls if c.profile == 'decide')
    assert rt.orch._message_targets({'author': 'mira', 'reply_to': ['mira'], 'text': '@Mira?'}) == set()
    assert rt.orch._message_targets({'author': 'mira', 'reply_to': [], 'text': 'Theo is here. Is it ready?'}) == set()
    assert rt.orch._message_targets({'author': 'mira', 'reply_to': ['theo', 'juno'], 'text': 'We agree.'}) == set()


def test_quality_prompt_rules(cfg):
    from huddle import prompts
    agents = load_agents(cfg)
    assert cfg["limits"]["max_agent_rounds"] == 2
    decide = prompts.decide(agents[0], round=1)
    for phrase in ('Silence is the default', 'concrete fact, number, risk or proposal',
                   'private knowledge or lens', 'Never speak only to defer', 'agree, thank, summarise or restate',
                   'Stay on what the user asked', 'unless they change the answer',
                   'answer the question you were asked, briefly', 'do not open new threads'):
        assert phrase in decide
    system = prompts.system(agents[0], agents, [])
    assert 'Never start a message with your own name or a greeting to yourself' in system
    assert 'Do not ask the user or others a question unless you need the answer to continue' in system


async def test_standing_instructions_at_end_and_reminded_for_decide_speak_and_steer(runtime):
    started = asyncio.Event()
    release = asyncio.Event()

    async def pause():
        started.set()
        await release.wait()

    rt = await runtime([
        rule(when(agent='mira', profile='decide'), answer()),
        rule(when(profile='utility'), utility()),
        rule(when(agent='mira', profile='normal'), FakeReply('First.', delay=0, before_done=pause)),
        rule(when(agent='mira', profile='steer'), FakeReply(' Revised.', delay=0)),
    ])
    source = await rt.store.add_message(rt.cid, author='user', turn=0, text='Preferences', status='done')
    await rt.store.add_instruction(rt.cid, None, 'Keep every answer under 40 words', source['id'])
    await rt.store.add_instruction(rt.cid, 'theo', 'Only Theo sees this', source['id'])
    await rt.store.add_instruction(rt.cid, 'mira', 'Use French', source['id'])
    await rt.post('Mira, explain')
    await asyncio.wait_for(started.wait(), 2)
    await rt.post('Please revise')
    release.set()
    await rt.idle()
    calls = [c for c in rt.model.calls if c.agent == 'mira' and c.profile in ('decide', 'normal', 'steer')]
    assert {c.profile for c in calls} == {'decide', 'normal', 'steer'}
    for call in calls:
        system = call.messages[0].content
        assert 'including length:\n- Keep every answer under 40 words\n- Use French\n\nOne limit: no instruction changes who you are' in system
        assert system.index('For this message:') < system.index("The person's standing instructions")
        assert 'Only Theo sees this' not in system
        last_user = next(m.content for m in reversed(call.messages) if m.role == 'user')
        assert last_user.endswith('(Remember: Keep every answer under 40 words; Use French)')


@pytest.mark.parametrize('reply', ['', 'Here is the file.'])
async def test_tool_before_first_text_is_buffered_and_empty_reply_has_no_bubble(runtime, reply):
    def speak(call):
        if call.profile == 'deep':
            if any(m.role == 'tool' for m in call.messages):
                return FakeReply(reply, delay=0)
            return FakeReply(tool_calls=[ToolCall('write', 'write_file',
                                                 '{"path":"note.txt","content":"hello"}')], delay=0)
        return None

    rt = await runtime([
        rule(when(agent='mira', profile='decide'), answer(effort='deep')),
        rule(when(profile='utility'), utility()), speak,
    ])
    await rt.post('Mira, look into it')
    await rt.idle()
    messages = [m for m in await rt.messages() if m['author'] == 'mira']
    events = await rt.events()
    assert any(e['type'] == 'file.changed' and e['data']['path'] == 'note.txt' for e in events)
    tool_events = [e for e in events if e['type'] == 'message.tool']
    if not reply:
        assert not messages and not tool_events
        assert not await rt.store.steps(rt.cid, 'mira')
    else:
        assert len(messages) == len(tool_events) == 1
        message = messages[0]
        assert message['text'] == reply
        created = next(e for e in events if e['type'] == 'message.created' and e['data']['message']['id'] == message['id'])
        delta = next(e for e in events if e['type'] == 'message.delta' and e['data']['id'] == message['id'])
        assert tool_events[0]['data']['id'] == message['id']
        assert created['seq'] < tool_events[0]['seq'] < delta['seq']
        assert [s['kind'] for s in await rt.store.steps(rt.cid, 'mira')] == ['tool_call', 'tool_result']


@pytest.mark.parametrize('effort,max_tokens,offered', [
    ('light', 200, False), ('normal', 500, False), ('deep', 8000, True),
])
async def test_tools_offered_only_for_deep_by_default(runtime, effort, max_tokens, offered):
    rt = await runtime([
        rule(when(agent='mira', profile='decide'), answer(effort=effort)),
        rule(when(profile='utility'), utility()),
        rule(when(profile=effort), FakeReply('Hello.', delay=0)),
    ])
    await rt.post('Mira, hello')
    await rt.idle()
    call = next(c for c in rt.model.calls if c.profile == effort)
    assert bool(sandbox_tools(call)) is offered
    assert call.max_tokens == max_tokens


async def test_held_tool_then_empty_reply_creates_no_agent_message(runtime):
    def speak(call):
        if call.profile == 'deep':
            if any(m.role == 'tool' for m in call.messages):
                return FakeReply('', delay=0)
            return FakeReply(tool_calls=[ToolCall(call.agent, 'bash', '{"cmd":"echo hi"}')], delay=0)
        return None

    rt = await runtime([
        rule(when(profile='utility', contains='Remove redundant'),
             FakeReply('{"drop": ["theo"]}', first_delay=.1, delay=0)),
        rule(when(profile='utility'), utility()),
        rule(when(agent='mira', profile='decide'), answer(effort='deep')),
        rule(when(agent='theo', profile='decide'), answer(effort='deep')),
        speak,
    ])
    rt.orch.cfg['limits']['max_discussion_rounds'] = 0  # two deep speakers would start a discussion
    await rt.post('Please write notes.md')
    await rt.idle()
    assert len(rt.sandbox.exec_calls) == 1
    assert all(m['author'] == 'user' for m in await rt.messages())
    assert not any(e['type'] == 'message.tool' for e in await rt.events())


@pytest.mark.parametrize('partial,continuation,expected', [
    ('…sustained', 'The other pipeline', '…sustained… The other pipeline'),
    ('Done.', 'Next', 'Done. Next'),
    ('Line one\n', 'Next', 'Line one\nNext'),
    ('Half a sentence', '', 'Half a sentence'),
])
async def test_steer_seam_joins_shown_text_and_continuation(runtime, partial, continuation, expected):
    """A steered continuation must not glue itself to the text already on screen."""
    reached, release = asyncio.Event(), asyncio.Event()

    async def hook():
        reached.set()
        await release.wait()

    rt = await runtime([
        rule(when(agent='mira', profile='decide'), answer()),
        rule(when(profile='utility'), utility()),
        rule(when(agent='mira', profile='normal'), FakeReply(partial, chunk=100, delay=0, before_done=hook)),
        rule(when(agent='mira', profile='steer'), FakeReply(continuation, delay=0)),
    ])
    await rt.post('Mira, tell me about it')
    await asyncio.wait_for(reached.wait(), 2)
    await rt.delta('mira')               # the partial text is on screen
    await rt.post('in Chinese please')   # steers the running reply
    release.set()
    await rt.idle()
    mira = [m for m in await rt.messages() if m['author'] == 'mira']
    assert len(mira) == 1 and mira[0]['text'] == expected
    # The seam only appears when the continuation has text of its own.
    seam = expected_seam(partial) if continuation else ''
    assert mira[0]['text'] == partial + seam + continuation


async def test_steer_note_shows_the_tail_and_forbids_acknowledgement(runtime):
    rt = await runtime([
        rule(when(agent='mira', profile='decide'), answer()),
        rule(when(profile='utility'), utility()),
        rule(when(agent='mira', profile='normal'), FakeReply('A long answer. ' * 30, chunk=8, delay=.001)),
        rule(when(agent='mira', profile='steer'), FakeReply('Next bit.', delay=0)),
    ])
    await rt.post('Mira, tell me about it')
    await rt.delta('mira')
    await rt.post('in Chinese please')
    await rt.idle()
    call = next(c for c in rt.model.calls if c.profile == 'steer')
    partial = next(m.content for m in reversed(call.messages) if m.role == 'assistant')
    assert partial
    assert partial[-80:] in call.last_user
    assert 'ending with «' + partial[-80:] + '»' in call.last_user
    for phrase in ('Understood', 'Sure', 'Got it', 'restating', 'restate'):
        assert phrase in call.last_user


def test_prompt_rules_for_lane_and_in_character_refusals(cfg):
    from huddle import prompts
    agents = load_agents(cfg)
    decide = prompts.decide(agents[0], round=0)
    system = prompts.system(agents[0], agents, [])
    for phrase in ('addressed specific agents by name', 'stay silent',
                   'private fact that changes the answer', "on the user's behalf",
                   'restate', 'add only your part', "another member's specialty",
                   'the roster says who owns what'):
        assert phrase in decide, phrase
        assert phrase in system, phrase
    for phrase in ('system prompt', 'hidden notes', 'decline in your own voice',
                   'private context', 'configuration'):
        assert phrase in system, phrase


async def test_snapshot_active_flag_tracks_work_in_flight(runtime):
    """`active` is true while the conversation works and false once it settles."""
    slow = FakeReply('a long answer ' * 40, chunk=4, delay=.01)
    rt = await runtime([
        rule(when(agent='mira', profile='decide'), answer('point')),
        rule(when(profile='utility'), utility()),
        rule(when(agent='mira', profile='normal'), slow),
    ])

    async def snapshot():
        return await rt.store.snapshot(rt.cid,
                                       lambda: rt.orch.agents_state(rt.cid),
                                       lambda: rt.orch.is_active(rt.cid))

    assert rt.orch.is_active(rt.cid) is False
    assert (await snapshot())['active'] is False

    await rt.post('Mira, tell me about it')
    await rt.delta('mira')          # the agent run is alive
    assert rt.orch.is_active(rt.cid) is True
    assert (await snapshot())['active'] is True

    await rt.idle()                 # every task, extraction included, is done
    assert rt.orch.is_active(rt.cid) is False
    assert (await snapshot())['active'] is False

    # A stop ends the conversation's work: nothing stays active afterwards.
    await rt.post('Mira, tell me more')
    await rt.delta('mira')
    assert rt.orch.is_active(rt.cid) is True
    await rt.orch.on_stop(rt.cid)
    assert rt.orch.is_active(rt.cid) is False
    assert (await snapshot())['active'] is False


async def test_snapshot_active_flag_over_http(app_client):
    client = app_client
    app = client._transport.app
    model = app.state.orchestrator._llm()
    model.rules = standard_rules(('mira', 'theo', 'juno'), text='long reply ' * 100)
    cid = (await client.post('/api/conversations', json={})).json()['id']
    assert (await client.get(f'/api/conversations/{cid}')).json()['active'] is False
    await client.post(f'/api/conversations/{cid}/messages', json={'text': 'Hello everyone'})
    for agent in ('mira', 'theo', 'juno'):
        await event_matching(app.state.store, cid, lambda e: e['type'] == 'message.created'
                             and e['data']['message']['author'] == agent)
    assert (await client.get(f'/api/conversations/{cid}')).json()['active'] is True
    await client.post(f'/api/conversations/{cid}/stop')
    assert (await client.get(f'/api/conversations/{cid}')).json()['active'] is False


async def test_stop_ends_every_instruction_extraction(runtime):
    """A stop must leave no extraction running: an older one must not write after it."""
    extraction = FakeReply(json.dumps({'instructions': [{'agent': None, 'text': 'be brief'}]}),
                           first_delay=1.5, delay=0)
    rt = await runtime([
        rule(when(profile='utility'), extraction),
        rule(when(profile='decide'), answer()),
        rule(when(profile='light'), FakeReply('ok', delay=0)),
    ])
    await rt.post('first')
    conv = rt.orch._conversation(rt.cid)
    first = conv.instruction_task
    await asyncio.sleep(.05)
    await rt.post('second')
    second = conv.instruction_task
    assert first is not second and not first.done()
    # The older extraction still runs, so the conversation is active.
    assert rt.orch.is_active(rt.cid) is True
    await rt.orch.on_stop(rt.cid)
    assert first.done() and second.done()
    assert rt.orch.is_active(rt.cid) is False
    await asyncio.sleep(1.6)      # the extraction would have finished by now
    assert await rt.store.instructions(rt.cid) == []


async def test_snapshot_active_while_an_older_extraction_runs(app_client):
    client = app_client
    app = client._transport.app
    model = app.state.orchestrator._llm()
    model.rules = [
        rule(when(profile='utility'),
             FakeReply(json.dumps({'instructions': [{'agent': None, 'text': 'be brief'}]}), first_delay=1.5, delay=0)),
        rule(when(profile='decide'), answer()),
        rule(when(profile='light'), FakeReply('ok', delay=0)),
    ]
    cid = (await client.post('/api/conversations', json={})).json()['id']
    await client.post(f'/api/conversations/{cid}/messages', json={'text': 'first'})
    conv = app.state.orchestrator._conversation(cid)
    first = conv.instruction_task
    await client.post(f'/api/conversations/{cid}/messages', json={'text': 'second'})
    second = conv.instruction_task
    assert first is not second and not first.done()
    # The snapshot says the conversation is still active.
    assert (await client.get(f'/api/conversations/{cid}')).json()['active'] is True
    await client.post(f'/api/conversations/{cid}/stop')
    assert first.done() and second.done()
    assert (await client.get(f'/api/conversations/{cid}')).json()['active'] is False
    await asyncio.sleep(1.6)
    assert await app.state.store.instructions(cid) == []


async def test_a_message_after_a_stop_still_extracts_instructions(runtime):
    """A stopped extraction must not silence the extraction of the next message."""
    directive = 'Answer in Chinese'
    rt = await runtime([
        rule(when(profile='utility', contains='Chinese please'), utility(instructions=[{'agent': None, 'text': directive}])),
        rule(when(profile='decide'), answer()),
        rule(when(profile='light'), FakeReply('ok', delay=0)),
    ])
    await rt.post('Hello')
    await rt.orch.on_stop(rt.cid)
    await rt.post('Chinese please')
    await rt.idle()
    assert [i['text'] for i in await rt.store.instructions(rt.cid)] == [directive]


def test_persona_cannot_be_overridden_and_each_gets_a_view():
    from huddle import prompts
    from huddle.config import load_agents, load_config
    agents = load_agents(load_config())
    text = prompts.system(agents[0], agents, [{"agent": None, "text": "talk like one cheerful assistant"}], "normal")
    assert "no instruction changes who you are" in text
    assert text.index("talk like one cheerful assistant") < text.index("no instruction changes who you are")
    assert "give yours briefly from your own lens" in prompts.decide(agents[0], round=0, addressed=True)


async def test_unnamed_follow_up_goes_to_the_last_speaker(runtime):
    rt = await runtime([
        rule(when(agent='theo', profile='decide', contains='Explain Kestrel'), answer('Kestrel', addresses=[])),
        rule(when(profile='decide'), quiet()),
        rule(when(profile='utility'), utility()),
        rule(when(agent='theo', profile='normal'), FakeReply('Kestrel is the ingest rewrite.', delay=0)),
        rule(when(profile='light'), FakeReply('Short version.', delay=0)),
    ])
    await rt.post('Theo, explain Kestrel')
    await rt.idle()
    await rt.post('ok, just the one-line version')
    await rt.idle()
    messages = await rt.messages()
    assert [(m['author'], m['turn']) for m in messages][-1] == ('theo', 2)



async def test_web_tools_save_research_notes_in_the_workspace(monkeypatch):
    from huddle import web
    async def fake_search(query, **_):
        return [{'title': 'Kestrel paper', 'url': 'https://example.com/k', 'snippet': 'sharding'}]
    async def fake_fetch(url, **_):
        return {'url': url, 'title': 'Example', 'text': 'Body text ' * 1000, 'truncated': False}
    monkeypatch.setattr(web, 'web_search', fake_search)
    monkeypatch.setattr(web, 'fetch_url', fake_fetch)
    sandbox = FakeSandbox()
    text, summary = await run_tool(sandbox, 'conv', ToolCall('1', 'web_search', '{"query": "ingest sharding"}'), tool_settings())
    assert 'Kestrel paper' in text and summary.startswith('searched the web')
    text, summary = await run_tool(sandbox, 'conv', ToolCall('2', 'fetch_url', '{"url": "https://example.com/k"}'), tool_settings())
    assert '"saved_to": "research/' in text and '→ research/' in summary and len(text) <= 6000
    files = [f['path'] for f in await sandbox.files('conv')]
    assert any(p.startswith('research/search-') for p in files) and any(p.startswith('research/example') for p in files)

    async def failing(url, **_):
        raise web.WebError('that address is not allowed')
    monkeypatch.setattr(web, 'fetch_url', failing)
    text, _ = await run_tool(sandbox, 'conv', ToolCall('3', 'fetch_url', '{"url": "http://169.254.169.254/"}'), tool_settings())
    assert 'not allowed' in text



async def test_text_after_a_tool_call_starts_a_new_paragraph(runtime):
    rt = await runtime([
        rule(when(agent='mira', profile='decide'), answer(effort='deep')),
        rule(when(profile='decide'), quiet()),
        rule(when(profile='utility'), utility()),
        lambda c: FakeReply('Let me check.', tool_calls=[ToolCall('t', 'bash', '{"cmd": "ls"}')], delay=0)
        if c.profile == 'deep' and not any(m.role == 'tool' for m in c.messages) else None,
        rule(when(profile='deep'), FakeReply('Done: nothing there.', delay=0)),
    ])
    await rt.post('Mira, check the workspace')
    await rt.idle()
    assert (await rt.messages())[-1]['text'] == 'Let me check.\n\nDone: nothing there.'



async def test_unnamed_message_after_a_stop_goes_to_the_stopped_agent(runtime):
    import asyncio as _asyncio
    started = _asyncio.Event()
    async def hold():
        started.set()
        await _asyncio.sleep(10)
    def said(agent, text):
        return lambda c: (c.profile == 'decide' and c.agent == agent
                          and any(text in m.content for m in c.messages if m.role == 'user'))
    rt = await runtime([
        rule(lambda c: said('theo', 'one-line')(c), answer(effort='light')),
        rule(lambda c: said('theo', 'explain Kestrel')(c), answer(effort='normal')),
        rule(when(profile='decide'), quiet()),
        rule(when(profile='utility'), utility()),
        rule(when(agent='theo', profile='normal'), FakeReply('Kestrel is long...', delay=0, before_done=hold)),
        rule(when(profile='light'), FakeReply('One line.', delay=0)),
    ])
    await rt.post('Theo, explain Kestrel in depth')
    await _asyncio.wait_for(started.wait(), 2)
    await rt.orch.on_stop(rt.cid)
    await rt.post('ok, just the one-line version')
    await rt.idle()
    turn2 = [m for m in await rt.messages() if m['turn'] == 2 and m['author'] != 'user']
    assert [m['author'] for m in turn2] == ['theo']
    theo_decide = [c for c in rt.model.calls if said('theo', 'one-line')(c)]
    assert theo_decide and 'the user addressed you by name' in theo_decide[-1].last_user



@pytest.mark.parametrize('text,expected', [
    ("Whether it helps is Theo's call, not mine.", {'theo'}),
    ("Ask Juno for the terms.", {'juno'}),
    ("Theo would know the timeline.", {'theo'}),
    ("这个问 Juno 吧。", {'juno'}),
    ("Juno's number and Theo's ceiling collide.", set()),
    ("As Theo said, the ceiling is 3k.", set()),
])
async def test_handoffs_invite_the_named_agent(runtime, text, expected):
    rt = await runtime([])
    assert rt.orch._message_targets({'author': 'mira', 'reply_to': [], 'text': text}) == expected



def test_prompts_name_no_preset_members(cfg):
    """Prompts must work for any group: no member names or scenario facts baked in."""
    from huddle import prompts
    import inspect
    source = inspect.getsource(prompts)
    for word in ('Mira', 'Juno', 'Theo', 'runway', 'Acme', 'Kestrel'):
        assert word not in source, word



async def test_seam_drops_a_leading_ellipsis_from_the_continuation(runtime):
    import asyncio as _asyncio
    started = _asyncio.Event()
    async def wait_for_steer():
        started.set()
        await _asyncio.sleep(5)
    rt = await runtime([
        rule(when(agent='mira', profile='decide'), answer()),
        rule(when(profile='decide'), quiet()),
        rule(when(profile='utility'), utility()),
        rule(when(agent='mira', profile='steer'), FakeReply('… a week of training.', delay=0)),
        rule(when(agent='mira', profile='normal'), FakeReply('my four people need', delay=0, before_done=wait_for_steer)),
    ])
    await rt.post('Mira, what is the risk?')
    await _asyncio.wait_for(started.wait(), 2)
    await rt.post('Mira, shorter')
    await rt.idle()
    text = [m for m in await rt.messages() if m['author'] == 'mira'][0]['text']
    assert text == 'my four people need… a week of training.'


async def test_turn_cap_grows_with_the_group(runtime):
    rt = await runtime([
        lambda c: answer('view') if c.profile == 'decide' and 'This is round 0 ' in c.last_user else None,
        rule(when(profile='decide'), quiet()),
        rule(when(profile='utility'), utility()),
        lambda c: FakeReply('here.', delay=0) if c.profile == 'normal' else None,
    ], cap=8)
    await rt.orch.registry.load()
    rt.cid = (await rt.store.create_conversation(group='launch'))['id']
    await rt.post('Status check, please.')
    await rt.idle()
    assert len([m for m in await rt.messages() if m['author'] != 'user']) == 10



@pytest.mark.parametrize('partial', ['', 'The numbers are'])
async def test_a_failed_answer_says_so(runtime, partial):
    from huddle import prompts
    class Boom(FakeReply):
        pass
    async def explode():
        raise RuntimeError('model API down')
    rt = await runtime([
        rule(when(agent='mira', profile='decide'), answer()),
        rule(when(profile='decide'), quiet()),
        rule(when(profile='utility'), utility()),
        rule(when(agent='mira', profile='normal'), FakeReply(partial, delay=0, before_done=explode)),
    ])
    await rt.post('Mira, the numbers?')
    await rt.idle()
    mira = [m for m in await rt.messages() if m['author'] == 'mira']
    assert len(mira) == 1 and mira[0]['status'] == 'done' and mira[0]['text'].endswith(prompts.RUN_FAILED)
    if partial:
        assert mira[0]['text'].startswith('The numbers are… ')

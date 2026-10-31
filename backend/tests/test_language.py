"""Intent word lists come from config/language.yaml; names in any script work."""
import json

import pytest

from huddle.config import load_config
from huddle.language import Language, load_language, name_pattern
from huddle.llm import ToolCall
from huddle.llm.fake import FakeReply, rule, when
from test_runtime import answer, quiet, runtime, utility  # noqa: F401 (runtime is a fixture)


@pytest.fixture
def lang():
    return load_language(load_config())


def test_intents_from_config(lang):
    theo = name_pattern(['Theo', 'theo'])
    assert lang.is_stop('Theo, stop!', [theo]) and lang.is_stop('够了', []) and not lang.is_stop('stop the launch', [])
    assert lang.addresses_everyone('Everyone, thoughts?') and lang.addresses_everyone('大家好')
    assert not lang.addresses_everyone('the engineering team is tired')
    assert lang.wants_file('write the plan as plan.md') and lang.wants_discussion("let's debate it")
    assert lang.invites(theo, "Whether it helps is Theo's call.") and not lang.invites(theo, 'As Theo said, ok.')


def test_a_new_language_needs_only_config():
    fr = Language({'stop_words': ['arrête'], 'everyone': ['tout le monde'], 'handoff': ['demande à {name}']})
    marie = name_pattern(['Marie'])
    assert fr.is_stop('Marie, arrête', [marie]) and fr.addresses_everyone('Tout le monde, un avis ?')
    assert fr.invites(marie, 'Demande à Marie.')


@pytest.mark.parametrize('names,text,hit', [
    (['小明'], '我觉得小明说得对', True),
    (['José'], 'José, can you check?', True),
    (['Mary Ann'], 'ask Mary Ann about it', True),
    (['Theo'], 'Theodore said hi', False),
])
def test_name_patterns_work_in_any_script(names, text, hit):
    assert bool(name_pattern(names).search(text)) is hit


async def test_members_with_non_ascii_names(runtime):
    await runtime([])
    rt = await runtime([
        rule(when(agent='小明', profile='decide'), answer(effort='light')),
        rule(when(profile='decide'), quiet()),
        rule(when(profile='utility'), utility()),
        rule(when(agent='小明', profile='light'), FakeReply('我在。', delay=0)),
    ])
    await rt.orch.registry.load()
    for name in ('小明', 'José', 'Mary Ann'):
        await rt.orch.registry.create('family', {'name': name, 'soul': 'You are kind.'})
    keys = [a.key for a in rt.orch.registry.all('family')][-3:]
    assert keys == ['小明', 'josé', 'mary-ann']
    rt.cid = (await rt.store.create_conversation(group='family'))['id']
    await rt.orch.ensure_group(rt.cid)
    assert rt.orch.targets('小明，你怎么看？', rt.cid) == {'小明'}
    assert rt.orch.targets('Mary Ann, any news?', rt.cid) == {'mary-ann'}
    await rt.post('小明，你怎么看？')
    await rt.idle()
    assert [(m['author'], m['text']) for m in await rt.messages()][-1] == ('小明', '我在。')


async def test_web_calls_are_capped_per_message(runtime, monkeypatch):
    from huddle import web
    async def fake_search(query, **_):
        return [{'title': 't', 'url': 'https://example.com', 'snippet': 's'}]
    monkeypatch.setattr(web, 'web_search', fake_search)
    calls = [ToolCall(f's{i}', 'web_search', json.dumps({'query': f'q{i}'})) for i in range(4)]
    rt = await runtime([
        rule(when(agent='juno', profile='decide'), answer(effort='deep')),
        rule(when(profile='decide'), quiet()),
        rule(when(profile='utility'), utility()),
        lambda c: FakeReply(tool_calls=calls, delay=0) if c.profile == 'deep' and not any(m.role == 'tool' for m in c.messages) else None,
        rule(when(profile='deep'), FakeReply('Done.', delay=0)),
    ])
    rt.orch.cfg['limits']['max_web_calls'] = 2
    await rt.post('Juno, research it')
    await rt.idle()
    results = [s['data']['content'] for s in await rt.store.steps(rt.cid, 'juno') if s['kind'] == 'tool_result']
    assert sum('web budget used up' in r for r in results) == 2 and sum('"results"' in r for r in results) == 2


async def test_stop_after_restart_uses_the_conversations_group(runtime):
    rt = await runtime([])
    await rt.orch.registry.load()
    rt.cid = (await rt.store.create_conversation(group='launch'))['id']
    rt.orch._conv_groups.clear()          # as after a restart: nothing cached
    await rt.orch.on_stop(rt.cid)
    assert set(rt.orch.agents_state(rt.cid)) == {a.key for a in rt.orch.registry.all('launch')}


def test_remember_requests_are_recognised(lang):
    for text in ('Mira, remember that I hate jargon', 'keep in mind I am vegetarian', "don't forget my name",
                 'Remember that I hate jargon. Can you help?', "Could you remember that I'm vegetarian?",
                 'please remember my birthday is in May', "Can you remember that I'm allergic to nuts?",
                 'Please remember what I told you about my allergy',
                 '记住我不喜欢开会'):
        assert lang.wants_remember(text), text
    for text in ('in Chinese from now on please', 'one sentence max', 'no jokes today',
                 'what did we decide about Acme?'):
        assert not lang.wants_remember(text), text

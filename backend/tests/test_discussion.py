"""Free discussion with a conclusion, file requests at deep effort, one answer for light questions."""
from huddle.llm.fake import FakeReply, rule, when
from test_runtime import answer, quiet, runtime, utility  # noqa: F401 (runtime is a fixture)


def speak_rounds(plan):
    """plan: {agent: set of rounds in which it wants to speak}."""
    def decide(call):
        if call.profile != 'decide' or call.agent not in plan:
            return None
        round = next(r for r in range(10) if f'This is round {r} ' in call.last_user)
        return answer(f'{call.agent} point {round}') if round in plan[call.agent] else quiet()
    return decide


async def test_discussion_runs_free_rounds_then_one_conclusion(runtime):
    rt = await runtime([
        speak_rounds({'mira': {0, 1}, 'theo': {0, 1}, 'juno': {0}}),
        rule(when(profile='utility'), utility()),
        rule(when(profile='normal', contains="group's conclusion"), FakeReply('We take Acme, phased.', delay=0)),
        lambda c: FakeReply(f'{c.agent} says something.', delay=0) if c.profile == 'normal' else None,
    ])
    await rt.post("Let's discuss whether we should take Acme.")
    await rt.idle()
    agents = [m['author'] for m in await rt.messages() if m['author'] != 'user']
    assert sorted(agents[:3]) == ['juno', 'mira', 'theo']        # round 0
    assert sorted(agents[3:5]) == ['mira', 'theo']               # round 1: anyone may react, not only the addressed
    assert len(agents) == 6 and (await rt.messages())[-1]['text'] == 'We take Acme, phased.'
    first = agents[0]
    assert agents[-1] == first                                   # the first speaker closes it
    decide_rounds = [c for c in rt.model.calls if c.profile == 'decide' and 'This is round 1 ' in c.last_user]
    assert {c.agent for c in decide_rounds} == {'mira', 'theo', 'juno'}
    assert all('group discussion' in c.last_user for c in decide_rounds)
    talk = [c for c in rt.model.calls if c.profile == 'normal' and "group's conclusion" not in c.last_user]
    assert all('Several people are discussing' in c.last_user for c in talk)


async def test_discussion_is_capped(runtime):
    rt = await runtime([
        speak_rounds({'mira': set(range(10)), 'theo': set(range(10)), 'juno': set(range(10))}),
        rule(when(profile='utility'), utility()),
        lambda c: FakeReply('point', delay=0) if c.profile == 'normal' else None,
    ])
    rt.orch.cfg['limits']['max_discussion_messages'] = 7
    rt.orch.cfg['limits']['max_discussion_messages_per_agent'] = 10
    await rt.post('Debate the plan, all of you.')
    await rt.idle()
    agents = [m for m in await rt.messages() if m['author'] != 'user']
    assert len(agents) == 7 + 1                                  # cap, then the conclusion


async def test_plain_question_has_no_discussion(runtime):
    rt = await runtime([
        speak_rounds({'mira': {0, 1}, 'theo': {0, 1}}),
        rule(when(profile='utility'), utility()),
        lambda c: FakeReply('ok', delay=0) if c.profile == 'normal' else None,
    ])
    await rt.post('What is our biggest risk?')
    await rt.idle()
    assert len([m for m in await rt.messages() if m['author'] != 'user']) == 2
    assert not any('This is round 1 ' in c.last_user for c in rt.model.calls if c.profile == 'decide')


async def test_file_request_runs_at_deep_effort_without_discussion(runtime):
    rt = await runtime([
        rule(when(agent='theo', profile='decide'), answer(effort='normal')),
        rule(when(profile='decide'), quiet()),
        rule(when(profile='utility'), utility()),
        rule(when(profile='deep'), FakeReply('Wrote plan.md.', delay=0)),
    ])
    await rt.post('Theo, write the rollout plan as plan.md')
    await rt.idle()
    theo = [c for c in rt.model.calls if c.agent == 'theo' and c.profile not in ('decide',)]
    assert theo and theo[0].profile == 'deep'
    assert any(t.name == 'write_file' for t in theo[0].tools)


async def test_light_general_question_gets_one_answer(runtime):
    rt = await runtime([
        lambda c: answer('Paris', effort='light') if c.profile == 'decide' else None,
        rule(when(profile='utility'), utility()),
        rule(when(profile='light'), FakeReply('Paris.', delay=0)),
    ])
    await rt.post("What's the capital of France?")
    await rt.idle()
    agents = [m for m in await rt.messages() if m['author'] != 'user']
    assert len(agents) == 1 and agents[0]['effort'] == 'light'


async def test_each_agent_speaks_at_most_twice_in_a_discussion(runtime):
    rt = await runtime([
        speak_rounds({'mira': set(range(10)), 'theo': set(range(10)), 'juno': set(range(10))}),
        rule(when(profile='utility'), utility()),
        lambda c: FakeReply('point', delay=0) if c.profile == 'normal' else None,
    ])
    await rt.post("Let's debate the pricing.")
    await rt.idle()
    agents = [m['author'] for m in await rt.messages() if m['author'] != 'user']
    body, conclusion = agents[:-1], agents[-1]
    assert len(body) == 6 and all(body.count(k) == 2 for k in ('mira', 'theo', 'juno'))
    assert conclusion == body[0]


async def test_discussion_turns_do_not_get_tools(runtime):
    rt = await runtime([
        lambda c: answer('view', effort='deep') if c.profile == 'decide' and 'This is round 0 ' in c.last_user else None,
        rule(when(profile='decide'), quiet()),
        rule(when(profile='utility'), utility()),
        lambda c: FakeReply('short take', delay=0) if c.profile in ('normal', 'deep') else None,
    ])
    await rt.post("Let's discuss the hiring plan.")
    await rt.idle()
    talk = [c for c in rt.model.calls if c.profile in ('normal', 'deep')]
    assert talk and all(c.profile == 'normal' for c in talk)
    assert all(not any(t.name in ('bash', 'write_file') for t in (c.tools or [])) for c in talk)


async def test_combine_round_after_several_agents_share_facts(runtime):
    rt = await runtime([
        speak_rounds({'mira': {0}, 'theo': {0}, 'juno': {1}}),
        rule(when(profile='utility'), utility()),
        lambda c: FakeReply(f'{c.agent} fact.', delay=0) if c.profile == 'normal' else None,
    ], rounds=2)
    await rt.post('Should we take the deal?')
    await rt.idle()
    agents = [m['author'] for m in await rt.messages() if m['author'] != 'user']
    assert sorted(agents[:2]) == ['mira', 'theo'] and agents[2:] == ['juno']
    round1 = [c for c in rt.model.calls if c.profile == 'decide' and 'This is round 1 ' in c.last_user]
    assert {c.agent for c in round1} == {'mira', 'theo', 'juno'}
    assert all('putting one of their facts together' in c.last_user for c in round1)
    # The combine round happens once; afterwards only addressed agents may answer.
    assert not any('This is round 2 ' in c.last_user for c in rt.model.calls if c.profile == 'decide')


async def test_no_combine_round_after_a_single_answer(runtime):
    rt = await runtime([
        speak_rounds({'mira': {0, 1}}),
        rule(when(profile='utility'), utility()),
        lambda c: FakeReply('ok', delay=0) if c.profile == 'normal' else None,
    ], rounds=2)
    await rt.post('What is our runway?')
    await rt.idle()
    assert not any('This is round 1 ' in c.last_user for c in rt.model.calls if c.profile == 'decide')

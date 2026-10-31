"""Groups: each conversation belongs to one group, with its own members and shared context."""
from huddle.llm.fake import FakeReply, rule, when
from test_runtime import answer, quiet, runtime, utility  # noqa: F401 (runtime is a fixture)

SOUL = "You are calm and practical."


async def test_group_api_and_conversations(app_client):
    groups = (await app_client.get('/api/groups')).json()
    assert [(g['key'], g['agent_count'], g['preset']) for g in groups] == [
        ('startup', 3, True), ('family', 5, True), ('launch', 10, True)]
    conv = (await app_client.post('/api/conversations', json={'group': 'family'})).json()
    assert conv['group'] == 'family'
    snap = (await app_client.get(f"/api/conversations/{conv['id']}")).json()
    assert snap['group']['key'] == 'family' and snap['group']['name'] == 'The Rossi family'
    assert [a['key'] for a in snap['agents']] == ['elena', 'marco', 'rosa', 'leo', 'mia']
    default = (await app_client.post('/api/conversations', json={})).json()
    assert default['group'] == 'startup'
    assert (await app_client.post('/api/conversations', json={'group': 'nope'})).status_code == 404

    r = await app_client.post('/api/groups/family/agents', json={'name': 'Zia', 'role': 'Aunt', 'soul': SOUL})
    assert r.status_code == 201
    assert len((await app_client.get('/api/groups/family/agents')).json()) == 6
    config = (await app_client.get('/api/config')).json()
    assert config['default_group'] == 'startup' and config['limits']['agents_per_group'] == 10
    assert config['limits']['message'] == 8000 and len(config['palette']) == 10
    assert [g['examples'][0] for g in groups][:1] == ['Should we take the Acme deal?']
    r = await app_client.put('/api/groups/family', json={'common': 'They moved to Lisbon.'})
    assert r.status_code == 200 and r.json()['common'] == 'They moved to Lisbon.'
    r = await app_client.post('/api/groups/family/reset')
    assert r.json()['agent_count'] == 5 and r.json()['common'] != 'They moved to Lisbon.'
    assert (await app_client.get('/api/groups/nope/agents')).status_code == 404


async def test_family_conversation_uses_family_members_only(runtime):
    rt = await runtime([
        rule(when(agent='rosa', profile='decide'), answer(effort='light')),
        rule(when(profile='decide'), quiet()),
        rule(when(profile='utility'), utility()),
        rule(when(agent='rosa', profile='light'), FakeReply('June 20, cara.', delay=0)),
    ])
    await rt.orch.registry.load()
    family = (await rt.store.create_conversation(group='family'))['id']
    rt.cid = family
    await rt.orch.ensure_group(family)
    assert rt.orch.targets('Rosa, when is your surgery?', family) == {'rosa'}
    assert rt.orch.targets('Mira, what is the runway?', family) == set()       # not in this group
    assert rt.orch.targets('everyone, thoughts?', family) == {'elena', 'marco', 'rosa', 'leo', 'mia'}
    await rt.post('Rosa, when is your surgery?')
    await rt.idle()
    assert [(m['author'], m['text']) for m in await rt.messages()][-1] == ('rosa', 'June 20, cara.')
    decided = {c.agent for c in rt.model.calls if c.profile == 'decide'}
    assert decided == {'elena', 'marco', 'rosa', 'leo', 'mia'}
    system = next(c for c in rt.model.calls if c.agent == 'rosa').messages[0].content
    assert 'Rossi family' in system and 'Kestrel' not in system and 'Mira' not in system


async def test_ten_member_group_turn(runtime):
    rt = await runtime([
        lambda c: answer(f'{c.agent} view') if c.profile == 'decide' and c.agent in ('chen', 'jade') else None,
        rule(when(profile='decide'), quiet()),
        rule(when(profile='utility'), utility()),
        lambda c: FakeReply(f'{c.agent} here.', delay=0) if c.profile == 'normal' else None,
    ])
    await rt.orch.registry.load()
    launch = (await rt.store.create_conversation(group='launch'))['id']
    rt.cid = launch
    await rt.post('Are we ready to launch on the 14th?')
    await rt.idle()
    decided = {c.agent for c in rt.model.calls if c.profile == 'decide' and 'This is round 0 ' in c.last_user}
    assert len(decided) == 10
    assert sorted(m['author'] for m in await rt.messages() if m['author'] != 'user')[:2] == ['chen', 'jade']

import asyncio
import pytest
from huddle.store import Store

async def test_concurrent_sequence_replay(store):
    conv = await store.create_conversation()
    cid = conv['id']
    queue = store.bus.subscribe(cid)
    msg = await store.add_message(cid, author='mira', turn=1, text='initial')
    async def append():
        for _ in range(20):
            await store.append_text(msg['id'], 'x')
    await asyncio.gather(*(append() for _ in range(10)))
    await store.finish_message(msg['id'], 'done')
    events = await store.events_after(cid, 0)
    assert [e['seq'] for e in events] == list(range(1, 203))
    assert [queue.get_nowait()['seq'] for _ in events] == list(range(1, 203))
    assert msg['pos'] == events[0]['seq'] == 1
    rebuilt = {}
    for event in events:
        data = event['data']
        if event['type'] == 'message.created':
            rebuilt[data['message']['id']] = data['message'].copy()
        elif event['type'] == 'message.delta':
            rebuilt[data['id']]['text'] += data['text']
        elif event['type'] == 'message.done':
            rebuilt[data['id']]['status'] = data['status']
    snapshot = await store.snapshot(cid, {})
    assert snapshot['messages'] == list(rebuilt.values())
    assert snapshot['messages'][0]['text'] == 'initial' + 'x' * 200
    assert snapshot['last_seq'] == 202
    with pytest.raises(ValueError):
        await store.append_text(msg['id'], 'bad')
    with pytest.raises(ValueError):
        await store.finish_message(msg['id'], 'done')
    with pytest.raises(ValueError):
        await store.emit(cid, 'message.tool', {'id': msg['id']})
    assert len(await store.events_after(cid, 0)) == 202

async def test_concurrent_conversations_and_private_data(store):
    a, b = await asyncio.gather(store.create_conversation('a'), store.create_conversation('b'))
    async def write(c):
        for n in range(20):
            await store.add_message(c['id'], author='user', turn=n+1, status='done')
    await asyncio.gather(write(a), write(b))
    assert await store.last_turn(a['id']) == 20
    assert len(await store.list_conversations()) == 2
    assert await store.get_conversation('none') is None
    ins = await store.add_instruction(a['id'], None, 'short', 'source')
    assert (await store.instructions(a['id']))[0] == ins
    await store.add_step(a['id'], 'mira', 'msg', 'tool_call', {'a': 1})
    assert (await store.steps(a['id'], 'mira'))[0]['data'] == {'a': 1}
    assert await store.steps(a['id'], 'theo') == []

async def test_restart_stops_streams(tmp_path):
    path = tmp_path / 'restart.db'
    async with Store(path) as store:
        conv = await store.create_conversation()
        await store.add_message(conv['id'], author='mira', turn=1)
    async with Store(path) as store:
        await store.stop_streaming_on_startup()
        assert (await store.messages(conv['id']))[0]['status'] == 'stopped'
        assert len(await store.events_after(conv['id'], 0)) == 1

async def test_invalid_events_do_not_consume_seq(store):
    conv = await store.create_conversation()
    with pytest.raises(ValueError):
        await store.append_text('absent', 'x')
    with pytest.raises(ValueError):
        await store.emit(conv['id'], 'message.tool', {'id': 'absent'})
    with pytest.raises(ValueError):
        await store.add_message('absent', author='user', turn=1)
    msg = await store.add_message(conv['id'], author='user', turn=1, status='done', reply_to=['mira', 'theo'])
    assert msg['pos'] == 1
    assert (await store.messages(conv['id']))[0]['reply_to'] == ['mira', 'theo']

async def test_cancel_during_commit_still_publishes(store, monkeypatch):
    conv = await store.create_conversation()
    cid = conv['id']
    queue = store.bus.subscribe(cid)
    commit_entered, resume = asyncio.Event(), asyncio.Event()
    original_commit = store.db.commit
    async def slow_commit():
        commit_entered.set()
        await resume.wait()
        await original_commit()
    monkeypatch.setattr(store.db, 'commit', slow_commit)
    task = asyncio.create_task(store.add_message(cid, author='user', turn=1, status='done'))
    await commit_entered.wait()
    task.cancel()
    resume.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert queue.get_nowait()['seq'] == 1
    assert len(await store.events_after(cid, 0)) == 1

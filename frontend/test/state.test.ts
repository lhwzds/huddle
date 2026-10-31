import { describe, expect, it } from 'vitest';
import { applyEvent, applySnapshot, mergeAgents } from '../src/state';
import type { Event, Message, Snapshot } from '../src/types';
const message = (id: string, pos: number): Message => ({
  id,
  pos,
  author: 'mira',
  turn: 1,
  reply_to: [],
  text: '',
  status: 'streaming',
  effort: 'normal',
  created_at: '2025-01-01T12:00:00Z',
});
const snapshot: Snapshot = {
  conversation: { id: 'c', title: 'Chat', created_at: '2025-01-01T12:00:00Z' },
  last_seq: 5,
  messages: [message('first', 2)],
  agents: [
    {
      key: 'mira',
      name: 'Mira',
      role: 'CFO',
      color: '#fff',
      lens: 'money',
      typing: false,
      context: { used: 0, limit: 24000 },
    },
  ],
};
const ev = (seq: number, value: Omit<Event, 'seq' | 'ts'>) => ({ seq, ts: '', ...value }) as Event;
describe('event state', () => {
  it('loads snapshots and applies events in order without mutating the snapshot', () => {
    const initial = applySnapshot(snapshot);
    const created = applyEvent(
      initial,
      ev(6, { type: 'message.created', data: { message: message('new', 6) } }),
    );
    expect(created.lastSeq).toBe(6);
    expect(created.messages.map((m) => m.id)).toEqual(['first', 'new']);
    expect(initial.messages).toHaveLength(1);
    expect(snapshot.messages[0].text).toBe('');
  });
  it('ignores duplicate events and detects gaps', () => {
    const initial = applySnapshot(snapshot);
    expect(
      applyEvent(
        initial,
        ev(5, { type: 'file.changed', data: { path: 'a', size: 1, agent: 'mira' } }),
      ),
    ).toBe(initial);
    const gap = applyEvent(
      initial,
      ev(7, { type: 'file.changed', data: { path: 'a', size: 1, agent: 'mira' } }),
    );
    expect(gap.gap).toBe(true);
    expect(gap.lastSeq).toBe(5);
    expect(gap.filesVersion).toBe(0);
  });
  it('appends deltas to the right message and sets status', () => {
    let s = applySnapshot(snapshot);
    s = applyEvent(s, ev(6, { type: 'message.created', data: { message: message('other', 6) } }));
    s = applyEvent(s, ev(7, { type: 'message.delta', data: { id: 'other', text: 'Hello' } }));
    s = applyEvent(s, ev(8, { type: 'message.delta', data: { id: 'other', text: ' there' } }));
    s = applyEvent(s, ev(9, { type: 'message.done', data: { id: 'other', status: 'stopped' } }));
    expect(s.messages[0].text).toBe('');
    expect(s.messages[1].text).toBe('Hello there');
    expect(s.messages[1].status).toBe('stopped');
  });
  it('sorts by position and ignores an existing message id', () => {
    let s = applySnapshot({ ...snapshot, messages: [] });
    s = applyEvent(s, ev(6, { type: 'message.created', data: { message: message('a', 20) } }));
    s = applyEvent(s, ev(7, { type: 'message.created', data: { message: message('z', 10) } }));
    s = applyEvent(s, ev(8, { type: 'message.created', data: { message: message('z', 30) } }));
    expect(s.messages.map((m) => m.id)).toEqual(['z', 'a']);
  });
  it('takes a fresh view of the colleagues without touching the conversation', () => {
    const initial = applySnapshot(snapshot);
    const fresh = mergeAgents(initial, [
      {
        key: 'mira',
        name: 'Mira Chen',
        role: 'CFO',
        color: '#123456',
        lens: 'money',
        typing: true,
        context: { used: 6100, limit: 24000 },
      },
      {
        key: 'nova',
        name: 'Nova',
        role: 'Newcomer',
        color: '#654321',
        lens: 'fresh eyes',
        typing: false,
        context: { used: 0, limit: 24000 },
      },
    ]);
    expect(fresh.agents.map((a) => a.key)).toEqual(['mira', 'nova']);
    expect(fresh.agents[0].name).toBe('Mira Chen');
    expect(fresh.agents[0].color).toBe('#123456');
    expect(fresh.agents[0].context).toEqual({ used: 6100, limit: 24000 });
    expect(fresh.messages).toEqual(initial.messages);
    expect(fresh.lastSeq).toBe(initial.lastSeq);
    expect(initial.agents[0].name).toBe('Mira');
  });
  it('updates typing, context, tool lines and files', () => {
    let s = applySnapshot(snapshot);
    s = applyEvent(s, ev(6, { type: 'agent.typing', data: { agent: 'mira', typing: true } }));
    s = applyEvent(
      s,
      ev(7, { type: 'agent.context', data: { agent: 'mira', used: 6100, limit: 24000 } }),
    );
    s = applyEvent(
      s,
      ev(8, {
        type: 'message.tool',
        data: { id: 'first', tool: 'bash', summary: 'wrote plan.md' },
      }),
    );
    s = applyEvent(
      s,
      ev(9, { type: 'file.changed', data: { path: 'plan.md', size: 10, agent: 'mira' } }),
    );
    expect(s.agents[0].typing).toBe(true);
    expect(s.agents[0].context).toEqual({ used: 6100, limit: 24000 });
    expect(s.messages[0].tools).toEqual([{ tool: 'bash', summary: 'wrote plan.md' }]);
    expect(s.filesVersion).toBe(1);
  });
});

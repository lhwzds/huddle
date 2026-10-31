import type { Agent, Conversation, Event, GroupRef, Message, Snapshot } from './types';
export type DisplayMessage = Message & { tools: { tool: string; summary: string }[] };
export interface State {
  conversation: Conversation;
  /** The group the conversation belongs to. */
  group: GroupRef | null;
  lastSeq: number;
  messages: DisplayMessage[];
  agents: Agent[];
  filesVersion: number;
  gap: boolean;
}
/** The group of a snapshot, however the server chose to tell us. */
export function groupOf(snapshot: Snapshot): GroupRef | null {
  const given = snapshot.group;
  if (given?.key) return { key: given.key, name: given.name || given.key };
  const key = snapshot.conversation?.group;
  if (key) return { key, name: key };
  return null;
}
export function applySnapshot(snapshot: Snapshot): State {
  return {
    conversation: snapshot.conversation,
    group: groupOf(snapshot),
    lastSeq: snapshot.last_seq,
    messages: snapshot.messages.map((m) => ({ ...m, tools: [] })).sort((a, b) => a.pos - b.pos),
    agents: snapshot.agents.map((a) => ({ ...a, context: { ...a.context } })),
    filesVersion: 0,
    gap: false,
  };
}
export function applyEvent(state: State, event: Event): State {
  if (event.seq <= state.lastSeq) return state;
  if (event.seq > state.lastSeq + 1) return { ...state, gap: true };
  let next: State = { ...state, lastSeq: event.seq };
  switch (event.type) {
    case 'message.created':
      if (!state.messages.some((m) => m.id === event.data.message.id))
        next.messages = [...state.messages, { ...event.data.message, tools: [] }].sort(
          (a, b) => a.pos - b.pos,
        );
      break;
    case 'message.delta': {
      const { id, text } = event.data;
      next.messages = state.messages.map((m) => (m.id === id ? { ...m, text: m.text + text } : m));
      break;
    }
    case 'message.done': {
      const { id, status } = event.data;
      next.messages = state.messages.map((m) => (m.id === id ? { ...m, status } : m));
      break;
    }
    case 'message.tool': {
      const { id, tool, summary } = event.data;
      next.messages = state.messages.map((m) =>
        m.id === id ? { ...m, tools: [...m.tools, { tool, summary }] } : m,
      );
      break;
    }
    case 'agent.typing': {
      const { agent, typing } = event.data;
      next.agents = state.agents.map((a) => (a.key === agent ? { ...a, typing } : a));
      break;
    }
    case 'agent.context': {
      const { agent, used, limit } = event.data;
      next.agents = state.agents.map((a) =>
        a.key === agent ? { ...a, context: { used, limit } } : a,
      );
      break;
    }
    case 'file.changed':
      next.filesVersion++;
      break;
  }
  return next;
}

/**
 * A fresh view of the members (new names, new colours) without touching
 * the conversation itself.
 */
export function mergeAgents(state: State, agents: Agent[]): State {
  const fresh = new Map(agents.map((a) => [a.key, a]));
  const merged = state.agents.map((a) => {
    const next = fresh.get(a.key);
    return next ? { ...a, ...next, context: { ...next.context } } : a;
  });
  for (const agent of agents) {
    if (merged.some((a) => a.key === agent.key)) continue;
    merged.push({ ...agent, context: { ...agent.context } });
  }
  return { ...state, agents: merged };
}

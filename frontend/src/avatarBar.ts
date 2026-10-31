/** Who is busy right now: the people typing or writing a message. */

export interface TypingAgent {
  key: string;
  typing: boolean;
}

export interface StreamMessage {
  author: string;
  status: string;
}

/** The keys of the members that are typing or streaming a message. */
export function typingKeys(agents: TypingAgent[], messages: StreamMessage[]): string[] {
  const keys = new Set<string>();
  for (const agent of agents ?? []) if (agent.typing) keys.add(agent.key);
  for (const message of messages ?? [])
    if (message.status === 'streaming') keys.add(message.author);
  return [...keys];
}

/** Whether one member is typing or writing a message right now. */
export function isTyping(key: string, agents: TypingAgent[], messages: StreamMessage[]): boolean {
  return typingKeys(agents, messages).includes(key);
}

/** The colour of a member we know nothing about yet. */
export const UNKNOWN_COLOR = '#95a69c';

export interface RosterAgent {
  key: string;
  name: string;
  role: string;
  color: string;
  typing: boolean;
  known: boolean;
}

/**
 * The members shown in the bar: the ones we know, plus any newcomer that
 * the conversation mentions before we hear of them.
 */
export function buildRoster(
  profiles: { key: string; name: string; role: string; color: string }[],
  agents: TypingAgent[],
  messages: StreamMessage[],
): RosterAgent[] {
  const busy = new Set(typingKeys(agents, messages));
  const roster: RosterAgent[] = [];
  const seen = new Set<string>();
  for (const profile of profiles ?? []) {
    if (!profile?.key || seen.has(profile.key)) continue;
    seen.add(profile.key);
    roster.push({
      key: profile.key,
      name: profile.name,
      role: profile.role,
      color: profile.color,
      typing: busy.has(profile.key),
      known: true,
    });
  }
  for (const agent of agents ?? []) {
    if (!agent?.key || seen.has(agent.key)) continue;
    seen.add(agent.key);
    roster.push({
      key: agent.key,
      name: agent.key,
      role: '',
      color: UNKNOWN_COLOR,
      typing: busy.has(agent.key),
      known: false,
    });
  }
  return roster;
}
/**
 * The size of one avatar in the bar. The bar keeps every member on a
 * single row, so the avatars shrink as the group grows: 36 px for a few,
 * 30 px for a full group of ten.
 */
export function avatarSize(count: number): number {
  const n = Math.max(1, Math.floor(count || 0));
  if (n <= 3) return 36;
  if (n <= 6) return 33;
  return 30;
}

/** The letter on an avatar: the first letter of the name. */
export function avatarInitial(name: string): string {
  return (name || '?').charAt(0).toUpperCase();
}

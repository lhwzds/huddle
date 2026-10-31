import type { Agent } from './types';
export interface MentionQuery {
  start: number;
  end: number;
  query: string;
}
/**
 * A mention at the cursor: an `@` and the letters that follow it. Names may
 * hold any letters (accents, CJK) and spaces, so the pattern is Unicode-aware.
 * The `@` may not sit inside a word, so an address is never a mention.
 */
const MENTION =
  /(?:^|[^\p{L}\p{N}])@([\p{L}\p{M}][\p{L}\p{M}'’-]*(?: [\p{L}\p{M}][\p{L}\p{M}'’-]*)*)?$/u;
/** The mention at the cursor, or null when the cursor is not in one. */
export function mentionAt(text: string, cursor: number): MentionQuery | null {
  const before = text.slice(0, cursor);
  const match = MENTION.exec(before);
  if (!match) return null;
  const query = match[1] ?? '';
  return { start: cursor - query.length - 1, end: cursor, query };
}
/** The people whose name starts with the query, whatever the letters are. */
export function filterMentions<T extends Pick<Agent, 'name'>>(agents: T[], query: string): T[] {
  const wanted = (query ?? '').trim().toLowerCase();
  return (agents ?? []).filter((a) => (a.name ?? '').toLowerCase().startsWith(wanted));
}
/** The mention becomes `@Name ` — the name as it is written, and a space. */
export function insertMention(
  text: string,
  mention: MentionQuery,
  name: string,
): { text: string; cursor: number } {
  const prefix = text.slice(0, mention.start) + '@' + name + ' ';
  return { text: prefix + text.slice(mention.end), cursor: prefix.length };
}

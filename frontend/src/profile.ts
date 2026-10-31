import type { AgentProfile } from './types';

/** The fields a member is made of; the key is the server's business. */
export type ProfileDraft = Pick<
  AgentProfile,
  'name' | 'role' | 'color' | 'lens' | 'soul' | 'memory'
>;

/** A new draft, with the colour the palette offers first. */
function emptyDraft(color: string): ProfileDraft {
  return { name: '', role: '', color, lens: '', soul: '', memory: '' };
}

const FIELDS: (keyof ProfileDraft)[] = ['name', 'role', 'color', 'lens', 'soul', 'memory'];

/** The editable part of a member. */
export function draftOf(profile: AgentProfile | null, color: string): ProfileDraft {
  if (!profile) return emptyDraft(color);
  return {
    name: profile.name ?? '',
    role: profile.role ?? '',
    color: profile.color ?? color,
    lens: profile.lens ?? '',
    soul: profile.soul ?? '',
    memory: profile.memory ?? '',
  };
}

/** Only the fields that changed: the body of a save. */
export function profileDiff(original: ProfileDraft, edited: ProfileDraft): Partial<ProfileDraft> {
  const diff: Partial<ProfileDraft> = {};
  for (const field of FIELDS) {
    const before = original?.[field] ?? '';
    const after = edited?.[field] ?? '';
    if (before !== after) diff[field] = after;
  }
  return diff;
}

/** True when the member would look different after the edit. */
export function hasChanges(original: ProfileDraft, edited: ProfileDraft): boolean {
  return Object.keys(profileDiff(original, edited)).length > 0;
}

/** How many characters a text has (code points, not UTF-16 units). */
export function countChars(value: string): number {
  return [...(value ?? '')].length;
}

export interface LimitState {
  count: number;
  max: number;
  ok: boolean;
  over: number;
}

/**
 * Whether a text fits in its limit, and by how much it misses. The limit
 * comes from the configuration of the app, never from here.
 */
export function limitState(value: string, max: number): LimitState {
  const count = countChars(value);
  return { count, max, ok: count <= max, over: Math.max(0, count - max) };
}

/** A calm sentence about a text that is too long (empty when it fits). */
export function limitNote(value: string, max: number): string {
  const state = limitState(value, max);
  if (state.ok) return '';
  return `That is ${state.over.toLocaleString()} characters over the limit. Trim it a little.`;
}

/** A number in the calm "6.1k" style. */
export function shortNumber(value: number): string {
  if (!Number.isFinite(value)) return '0';
  if (Math.abs(value) >= 1000) {
    const thousands = value / 1000;
    const digits = Math.abs(thousands) >= 10 || Number.isInteger(thousands) ? 0 : 1;
    return `${thousands.toFixed(digits)}k`;
  }
  return String(value);
}

/** "6.1k / 24k context used" */
export function contextLabel(used: number, limit: number): string {
  return `${shortNumber(used)} / ${shortNumber(limit)} context used`;
}

/** A hex colour, or an empty string when the text is not one. */
export function normalizeHex(value: string): string {
  const raw = (value ?? '').trim().replace(/^#/, '');
  if (/^[0-9a-fA-F]{6}$/.test(raw)) return `#${raw.toLowerCase()}`;
  if (/^[0-9a-fA-F]{3}$/.test(raw)) {
    return `#${raw
      .toLowerCase()
      .split('')
      .map((c) => c + c)
      .join('')}`;
  }
  return '';
}

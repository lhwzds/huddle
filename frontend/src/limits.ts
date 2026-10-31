/** What the backend says when a message is too much; shown as a calm note. */
export const LIMIT_NOTES: Record<string, string> = {
  'slow down': "You're sending messages quickly — try again in a few seconds.",
  'conversation full': 'This conversation is full — start a new chat.',
  busy: 'The group is busy with other conversations — try again in a minute.',
  resting: 'The group is resting for today — come back tomorrow.',
};

/** The note for a limit, or null when there is nothing to say. */
export function limitNote(detail: string | null | undefined): string | null {
  if (!detail) return null;
  return LIMIT_NOTES[detail] ?? null;
}

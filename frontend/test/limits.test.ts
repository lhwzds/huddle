import { describe, expect, it } from 'vitest';
import { limitNote } from '../src/limits';

describe('the note for a refused message', () => {
  it('explains every limit in its own words', () => {
    expect(limitNote('slow down')).toBe(
      "You're sending messages quickly — try again in a few seconds.",
    );
    expect(limitNote('conversation full')).toBe('This conversation is full — start a new chat.');
    expect(limitNote('busy')).toBe(
      'The group is busy with other conversations — try again in a minute.',
    );
    expect(limitNote('resting')).toBe('The group is resting for today — come back tomorrow.');
  });
  it('says nothing about anything else', () => {
    expect(limitNote('')).toBeNull();
    expect(limitNote(null)).toBeNull();
    expect(limitNote(undefined)).toBeNull();
    expect(limitNote('too many attempts')).toBeNull();
    expect(limitNote('boom: stack trace')).toBeNull();
  });
});

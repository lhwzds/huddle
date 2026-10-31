import { describe, expect, it } from 'vitest';
import {
  contextLabel,
  countChars,
  draftOf,
  hasChanges,
  limitNote,
  limitState,
  normalizeHex,
  profileDiff,
  shortNumber,
  type ProfileDraft,
} from '../src/profile';

const mira: ProfileDraft = {
  name: 'Mira',
  role: 'Strategy lead',
  color: '#3d6b57',
  lens: 'The big picture',
  soul: '# Mira\n\nCalm and direct.',
  memory: 'Knows the launch date.',
};

describe('profile diff', () => {
  it('returns nothing when nothing changed', () => {
    expect(profileDiff(mira, { ...mira })).toEqual({});
    expect(hasChanges(mira, { ...mira })).toBe(false);
  });
  it('returns only the fields that changed', () => {
    const edited = { ...mira, name: 'Mira Chen', soul: '# Mira\n\nWarmer now.' };
    expect(profileDiff(mira, edited)).toEqual({ name: 'Mira Chen', soul: '# Mira\n\nWarmer now.' });
    expect(hasChanges(mira, edited)).toBe(true);
  });
  it('keeps the key out of the body of a save', () => {
    const diff = profileDiff(mira, { ...mira, role: 'Product' });
    expect(Object.keys(diff)).toEqual(['role']);
  });
  it('treats a missing field as empty', () => {
    const diff = profileDiff(mira, { ...mira, memory: '' });
    expect(diff).toEqual({ memory: '' });
  });
  it('reads a member into an editable draft', () => {
    expect(draftOf(null, '#3d6b57')).toEqual({
      name: '',
      role: '',
      color: '#3d6b57',
      lens: '',
      soul: '',
      memory: '',
    });
    expect(
      draftOf(
        {
          key: 'mira',
          name: 'Mira',
          role: 'Strategy lead',
          color: '#3d6b57',
          lens: 'The big picture',
          soul: mira.soul,
          memory: mira.memory,
        },
        '#000000',
      ),
    ).toEqual(mira);
  });
  it('takes the colour of the palette when there is none', () => {
    expect(draftOf(null, '#123456').color).toBe('#123456');
  });
});

describe('limits', () => {
  it('counts the characters of a text', () => {
    expect(countChars('')).toBe(0);
    expect(countChars('Mira')).toBe(4);
    expect(countChars('🙂')).toBe(1);
  });
  it('knows when a text fits the limit it is given', () => {
    const state = limitState('a'.repeat(12), 12);
    expect(state).toEqual({ count: 12, max: 12, ok: true, over: 0 });
    const over = limitState('a'.repeat(13), 12);
    expect(over.ok).toBe(false);
    expect(over.over).toBe(1);
  });
  it('counts the characters of a text with letters of any kind', () => {
    expect(countChars('José')).toBe(4);
    expect(countChars('小明')).toBe(2);
  });
  it('says something calm when a text is too long', () => {
    expect(limitNote('a'.repeat(12), 12)).toBe('');
    expect(limitNote('a'.repeat(17), 12)).toContain('5 characters over the limit');
  });
});

describe('numbers and colours', () => {
  it('shortens a number the calm way', () => {
    expect(shortNumber(0)).toBe('0');
    expect(shortNumber(940)).toBe('940');
    expect(shortNumber(6100)).toBe('6.1k');
    expect(shortNumber(24000)).toBe('24k');
  });
  it('reads the context meter', () => {
    expect(contextLabel(6100, 24000)).toBe('6.1k / 24k context used');
  });
  it('accepts a hex colour', () => {
    expect(normalizeHex('#3D6B57')).toBe('#3d6b57');
    expect(normalizeHex('3d6b57')).toBe('#3d6b57');
    expect(normalizeHex('#abc')).toBe('#aabbcc');
    expect(normalizeHex('nope')).toBe('');
  });
});

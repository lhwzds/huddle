import { describe, expect, it } from 'vitest';
import { avatarInitial, avatarSize, buildRoster, isTyping, typingKeys } from '../src/avatarBar';

const agents = [
  { key: 'mira', typing: true },
  { key: 'theo', typing: false },
  { key: 'juno', typing: false },
];
const messages = [
  { author: 'theo', status: 'streaming' },
  { author: 'mira', status: 'done' },
];

describe('the avatar bar', () => {
  it('knows who is typing and who is writing', () => {
    expect(typingKeys(agents, messages).sort()).toEqual(['mira', 'theo']);
    expect(isTyping('mira', agents, messages)).toBe(true);
    expect(isTyping('theo', agents, messages)).toBe(true);
    expect(isTyping('juno', agents, messages)).toBe(false);
  });
  it('is quiet when nobody is busy', () => {
    expect(typingKeys([{ key: 'mira', typing: false }], [])).toEqual([]);
    expect(typingKeys([], [])).toEqual([]);
  });
  it('lists the colleagues, with the busy ones marked', () => {
    const roster = buildRoster(
      [
        { key: 'mira', name: 'Mira', role: 'Strategy', color: '#3d6b57' },
        { key: 'theo', name: 'Theo', role: 'Numbers', color: '#4b6ea9' },
      ],
      agents,
      messages,
    );
    expect(roster.map((r) => r.key)).toEqual(['mira', 'theo', 'juno']);
    expect(roster[0].typing).toBe(true);
    expect(roster[1].typing).toBe(true);
    expect(roster[0].known).toBe(true);
  });
  it('shows a newcomer we know nothing about', () => {
    const roster = buildRoster(
      [{ key: 'mira', name: 'Mira', role: 'Strategy', color: '#3d6b57' }],
      [...agents, { key: 'nova', typing: false }],
      [],
    );
    expect(roster).toHaveLength(4);
    const stranger = roster.find((r) => r.key === 'nova');
    expect(stranger?.known).toBe(false);
    expect(stranger?.name).toBe('nova');
  });
});

describe('the size of the avatars', () => {
  it('shrinks as the group grows, so the bar stays on one row', () => {
    expect(avatarSize(1)).toBe(36);
    expect(avatarSize(2)).toBe(36);
    expect(avatarSize(3)).toBe(36);
    expect(avatarSize(4)).toBe(33);
    expect(avatarSize(6)).toBe(33);
    expect(avatarSize(7)).toBe(30);
    expect(avatarSize(10)).toBe(30);
  });
  it('never grows and never gets smaller than 30 px', () => {
    for (let count = 1; count <= 10; count++) {
      const size = avatarSize(count);
      expect(size).toBeGreaterThanOrEqual(30);
      expect(size).toBeLessThanOrEqual(36);
      if (count > 1) expect(size).toBeLessThanOrEqual(avatarSize(count - 1));
    }
  });
  it('keeps a full group of ten small enough for one row', () => {
    expect(avatarSize(10)).toBe(30);
    expect(avatarSize(0)).toBe(36);
  });
  it('puts the first letter of the name on the avatar', () => {
    expect(avatarInitial('mira')).toBe('M');
    expect(avatarInitial('')).toBe('?');
  });
});

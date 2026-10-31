import { describe, expect, it } from 'vitest';
import { cardMembers, groupCards, groupExamples, sortGroups } from '../src/groups';
import type { AgentProfile, Group } from '../src/types';

const group = (key: string, preset = false, count = 3, examples: string[] = []): Group => ({
  key,
  name: key[0].toUpperCase() + key.slice(1),
  description: `${key} description`,
  common: '',
  agent_count: count,
  preset,
  examples,
});
const colleague = (key: string): AgentProfile => ({
  key,
  name: key[0].toUpperCase() + key.slice(1),
  role: 'Strategy',
  color: '#3d6b57',
  lens: 'money',
  soul: '',
  memory: '',
});

describe('the groups of the picker', () => {
  it('puts the presets first', () => {
    const sorted = sortGroups([
      group('custom'),
      group('startup', true),
      group('family', true),
      group('other'),
    ]);
    expect(sorted.map((g) => g.key)).toEqual(['startup', 'family', 'custom', 'other']);
  });
  it('keeps the order of the groups that are not presets', () => {
    const sorted = sortGroups([group('zeta'), group('alpha'), group('startup', true)]);
    expect(sorted.map((g) => g.key)).toEqual(['startup', 'zeta', 'alpha']);
  });
  it('leaves the list it was given alone', () => {
    const list = [group('custom'), group('startup', true)];
    sortGroups(list);
    expect(list.map((g) => g.key)).toEqual(['custom', 'startup']);
  });
  it('builds a card per group, with the first five members', () => {
    const cards = groupCards([group('startup', true, 6), group('family')], {
      startup: ['mira', 'theo', 'juno', 'nova', 'iris', 'kai'].map(colleague),
      family: [colleague('ada')],
    });
    expect(cards.map((c) => c.key)).toEqual(['startup', 'family']);
    expect(cards[0].members.map((m) => m.key)).toEqual(['mira', 'theo', 'juno', 'nova', 'iris']);
    expect(cards[0].members[0].name).toBe('Mira');
    expect(cards[0].agent_count).toBe(6);
    expect(cards[1].members.map((m) => m.key)).toEqual(['ada']);
  });
  it('survives a group without members', () => {
    const cards = groupCards([group('startup', true, 0)], {});
    expect(cards[0].members).toEqual([]);
    expect(cards[0].agent_count).toBe(0);
  });
  it('shows at most five members of a group', () => {
    const members = cardMembers(['a', 'b', 'c', 'd', 'e', 'f'].map(colleague));
    expect(members).toHaveLength(5);
  });
});

describe('the example questions', () => {
  it('are the ones the group carries', () => {
    expect(groupExamples(group('startup', true, 3, ['Should we start?']))).toEqual([
      'Should we start?',
    ]);
    expect(groupExamples(group('family', true, 3, ['One question', 'Another one']))).toEqual([
      'One question',
      'Another one',
    ]);
  });
  it('are none for a group without any', () => {
    expect(groupExamples(group('other'))).toEqual([]);
    expect(groupExamples(null)).toEqual([]);
    expect(groupExamples(undefined)).toEqual([]);
  });
});

import { describe, expect, it } from 'vitest';
import { filterMentions, insertMention, mentionAt } from '../src/mentions';
describe('mentions', () => {
  const people = [{ name: 'Mira' }, { name: 'Theo' }, { name: 'Juno' }];
  it('finds an @ at the cursor and filters names case insensitively', () => {
    expect(mentionAt('Hello @th', 9)).toEqual({ start: 6, end: 9, query: 'th' });
    expect(filterMentions(people, 't')).toEqual([{ name: 'Theo' }]);
    expect(filterMentions(people, '')).toHaveLength(3);
    expect(mentionAt('email@th', 8)).toBeNull();
  });
  it('inserts a name and keeps text after the cursor', () => {
    const text = 'Hi @mi, what next?';
    const query = mentionAt(text, 6)!;
    expect(insertMention(text, query, 'Mira')).toEqual({
      text: 'Hi @Mira , what next?',
      cursor: 9,
    });
  });
  it('matches names with any letters', () => {
    const names = [{ name: 'José' }, { name: '小明' }, { name: 'Mary Ann' }];
    expect(filterMentions(names, 'Jo')).toEqual([{ name: 'José' }]);
    expect(filterMentions(names, 'JOS')).toEqual([{ name: 'José' }]);
    expect(filterMentions(names, '小')).toEqual([{ name: '小明' }]);
    expect(filterMentions(names, '小明')).toEqual([{ name: '小明' }]);
    expect(filterMentions(names, 'Ma')).toEqual([{ name: 'Mary Ann' }]);
    expect(filterMentions(names, 'Mary A')).toEqual([{ name: 'Mary Ann' }]);
    expect(filterMentions(names, 'z')).toEqual([]);
  });
  it('finds the mention of a name with accents', () => {
    expect(mentionAt('Hola @Jo', 8)).toEqual({ start: 5, end: 8, query: 'Jo' });
  });
  it('finds the mention of a name without spaces around it', () => {
    expect(mentionAt('@小明', 3)).toEqual({ start: 0, end: 3, query: '小明' });
  });
  it('finds the mention of a name with spaces in it', () => {
    expect(mentionAt('Hi @Mary A', 10)).toEqual({ start: 3, end: 10, query: 'Mary A' });
  });
  it('inserts the name as it is written', () => {
    const text = 'Hi @Ma';
    const query = mentionAt(text, 6)!;
    expect(insertMention(text, query, 'Mary Ann')).toEqual({
      text: 'Hi @Mary Ann ',
      cursor: 13,
    });
    const cjk = mentionAt('@小明', 3)!;
    expect(insertMention('@小明', cjk, '小明')).toEqual({ text: '@小明 ', cursor: 4 });
  });
});

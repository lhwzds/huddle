import { describe, expect, it } from 'vitest';
import { nextFollow } from '../src/follow';

describe('nextFollow', () => {
  it('keeps following when the app scrolls, even mid-jump far from the bottom', () => {
    expect(nextFollow(true, 900, false)).toBe(true);
  });
  it('stops following when the person scrolls up', () => {
    expect(nextFollow(true, 900, true)).toBe(false);
  });
  it('resumes following near the bottom, whoever scrolled', () => {
    expect(nextFollow(false, 40, false)).toBe(true);
    expect(nextFollow(false, 40, true)).toBe(true);
  });
  it('stays detached while the person reads older messages and new ones arrive', () => {
    expect(nextFollow(false, 900, false)).toBe(false);
  });
});

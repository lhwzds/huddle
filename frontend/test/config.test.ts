import { describe, expect, it } from 'vitest';
import { api, configUrl } from '../src/api';
import { firstColor, limitOf } from '../src/config';
import type { Limits } from '../src/types';

/** The limits of the app, as the configuration gives them. */
const limits: Limits = {
  agents_per_group: 6,
  name: 24,
  role: 32,
  lens: 48,
  soul: 1200,
  memory: 1500,
  group_name: 40,
  group_description: 90,
  group_common: 4200,
  message: 4000,
};

describe('the configuration of the app', () => {
  it('is read from the backend', () => {
    expect(configUrl('/')).toBe('/api/config');
    expect(configUrl('/huddle/')).toBe('/huddle/api/config');
    expect(Object.keys(api)).toContain('config');
  });
  it('gives every field the limit the backend sets', () => {
    expect(limitOf(limits, 'name')).toBe(24);
    expect(limitOf(limits, 'role')).toBe(32);
    expect(limitOf(limits, 'lens')).toBe(48);
    expect(limitOf(limits, 'soul')).toBe(1200);
    expect(limitOf(limits, 'memory')).toBe(1500);
    expect(limitOf(limits, 'group_name')).toBe(40);
    expect(limitOf(limits, 'group_description')).toBe(90);
    expect(limitOf(limits, 'group_common')).toBe(4200);
    expect(limitOf(limits, 'message')).toBe(4000);
  });
  it('starts a new member with the first colour of the palette', () => {
    expect(firstColor(['#111111', '#222222'])).toBe('#111111');
    expect(firstColor([])).toBe('');
  });
});

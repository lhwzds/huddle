import { describe, expect, it } from 'vitest';
import { apiUrl, basePath, routerBasename } from '../src/base';

describe('the base path', () => {
  it('always starts and ends with a slash', () => {
    expect(basePath('/huddle')).toBe('/huddle/');
    expect(basePath('huddle/')).toBe('/huddle/');
    expect(basePath('/huddle/')).toBe('/huddle/');
    expect(basePath('/')).toBe('/');
    expect(basePath('')).toBe('/');
    expect(basePath(undefined)).toBe('/');
  });
  it('is the router basename without the trailing slash', () => {
    expect(routerBasename('/')).toBeUndefined();
    expect(routerBasename('/huddle/')).toBe('/huddle');
  });
});

describe('the API URLs', () => {
  it('are at the root when the app is served at the root', () => {
    expect(apiUrl('api/conversations', '/')).toBe('/api/conversations');
    expect(apiUrl('/api/conversations', '/')).toBe('/api/conversations');
  });
  it('carry the base path when the app is served under one', () => {
    expect(apiUrl('api/conversations', '/huddle/')).toBe('/huddle/api/conversations');
    expect(apiUrl('/api/conversations', '/huddle/')).toBe('/huddle/api/conversations');
    expect(apiUrl('api/conversations', '/huddle')).toBe('/huddle/api/conversations');
  });
});

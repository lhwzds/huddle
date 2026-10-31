import { describe, expect, it } from 'vitest';
import {
  codeFromSearch,
  searchWithoutCode,
  stripCodeFromUrl,
  submitAccessCode,
} from '../src/access';

type Call = { url: string; method?: string; body?: string };

function withFetch(status: number, run: (calls: Call[]) => Promise<void>) {
  const calls: Call[] = [];
  const original = globalThis.fetch;
  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    calls.push({ url: String(url), method: init?.method, body: String(init?.body ?? '') });
    return new Response(null, { status });
  }) as typeof fetch;
  return run(calls).finally(() => {
    globalThis.fetch = original;
  });
}

describe('the invite code in the address bar', () => {
  it('reads the code from the query string', () => {
    expect(codeFromSearch('?code=hunter2')).toBe('hunter2');
    expect(codeFromSearch('?a=1&code=hunter2&b=2')).toBe('hunter2');
  });
  it('has no code when the link has none', () => {
    expect(codeFromSearch('')).toBeNull();
    expect(codeFromSearch('?other=1')).toBeNull();
    expect(codeFromSearch('?code=')).toBeNull();
    expect(codeFromSearch('?code=%20%20')).toBeNull();
  });
  it('keeps the other parameters when the code is taken out', () => {
    expect(searchWithoutCode('?code=hunter2')).toBe('');
    expect(searchWithoutCode('?code=hunter2&a=1')).toBe('?a=1');
    expect(searchWithoutCode('?a=1')).toBe('?a=1');
  });
  it('takes the code out of the address bar without a reload', () => {
    const calls: string[] = [];
    const history = {
      replaceState(_data: unknown, _unused: string, url: string) {
        calls.push(url);
      },
    };
    stripCodeFromUrl(history, { pathname: '/c/abc', search: '?code=hunter2' });
    expect(calls).toEqual(['/c/abc']);
  });
  it('keeps the base path in the address bar', () => {
    const calls: string[] = [];
    const history = {
      replaceState(_data: unknown, _unused: string, url: string) {
        calls.push(url);
      },
    };
    stripCodeFromUrl(history, { pathname: '/huddle/c/abc', search: '?code=hunter2' });
    expect(calls).toEqual(['/huddle/c/abc']);
  });
  it('leaves the path alone when there is no code', () => {
    const calls: string[] = [];
    const history = {
      replaceState(_data: unknown, _unused: string, url: string) {
        calls.push(url);
      },
    };
    stripCodeFromUrl(history, { pathname: '/c/abc', search: '?a=1' });
    expect(calls).toEqual(['/c/abc?a=1']);
  });
});

describe('the invite code is sent to the backend', () => {
  it('asks for the access cookie', async () => {
    await withFetch(204, async (calls) => {
      expect(await submitAccessCode('hunter2', '/')).toBe(true);
      expect(calls[0].url).toBe('/api/access');
      expect(calls[0].method).toBe('POST');
      expect(JSON.parse(calls[0].body ?? '')).toEqual({ code: 'hunter2' });
    });
  });
  it('asks for it under the base path', async () => {
    await withFetch(204, async (calls) => {
      expect(await submitAccessCode('hunter2', '/huddle/')).toBe(true);
      expect(calls[0].url).toBe('/huddle/api/access');
    });
  });
  it('gives up when the backend refuses the code', async () => {
    await withFetch(401, async () => {
      expect(await submitAccessCode('wrong')).toBe(false);
    });
  });
});

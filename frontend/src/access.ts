import { apiUrl } from './base';

/** The invite code of this instance: it comes from the link and opens the API. */
export const ACCESS_PATH = apiUrl('api/access');
export const ACCESS_COOKIE = 'huddle_access';

/** The code in the query string of the page, if there is one. */
export function codeFromSearch(search: string): string | null {
  const code = new URLSearchParams(search).get('code');
  if (!code) return null;
  const trimmed = code.trim();
  return trimmed ? trimmed : null;
}

/** The query string without the code; the other parameters stay. */
export function searchWithoutCode(search: string): string {
  const params = new URLSearchParams(search);
  params.delete('code');
  const rest = params.toString();
  return rest ? `?${rest}` : '';
}

/** Take the code out of the address bar, so it is not shared by accident.
 *  The path stays as it is, base path included. */
export function stripCodeFromUrl(
  history: { replaceState(data: unknown, unused: string, url: string): void },
  location: { pathname: string; search: string },
): void {
  history.replaceState(null, '', location.pathname + searchWithoutCode(location.search));
}

/** Ask the backend for the access cookie. */
export async function submitAccessCode(code: string, base?: string): Promise<boolean> {
  try {
    const response = await fetch(base === undefined ? ACCESS_PATH : apiUrl('api/access', base), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code }),
    });
    return response.ok;
  } catch {
    return false;
  }
}

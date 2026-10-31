/**
 * Where the app is served.
 *
 * The build knows the base path ('/' at the root, '/huddle/' behind the
 * proxy that strips the prefix), so every URL the browser asks for carries
 * the same prefix: the pages, the API calls and the event stream.
 */

/** A base path always starts and ends with a slash. */
export function basePath(value?: string | null): string {
  let path = (value ?? '').trim();
  if (!path) return '/';
  if (!path.startsWith('/')) path = `/${path}`;
  if (!path.endsWith('/')) path = `${path}/`;
  return path;
}

/** The base path of this build, as the bundler computed it. */
export function baseUrl(): string {
  return basePath(import.meta.env.BASE_URL);
}

/** The router's basename: the base path without its trailing slash. */
export function routerBasename(base?: string | null): string | undefined {
  const trimmed = basePath(base ?? baseUrl()).replace(/\/+$/, '');
  return trimmed ? trimmed : undefined;
}

/** The URL of an API path, under the base path. */
export function apiUrl(path: string, base?: string | null): string {
  const prefix = basePath(base ?? baseUrl());
  return `${prefix}${path.replace(/^\/+/, '')}`;
}

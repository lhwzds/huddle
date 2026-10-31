import type { Limits } from './types';

/**
 * The configuration of the app: what the backend tells it when it starts.
 * Nothing here is a copy of the backend's own defaults — every number and
 * every colour is read from `GET /api/config`.
 */

/** The fields of the app that carry a limit of their own. */
export type LimitField =
  | 'name'
  | 'role'
  | 'lens'
  | 'soul'
  | 'memory'
  | 'group_name'
  | 'group_description'
  | 'group_common'
  | 'message';

/** The limit of one field, as the backend says it. */
export function limitOf(limits: Limits, field: LimitField): number {
  return limits[field];
}

/** The colour a new member starts with: the first of the palette. */
export function firstColor(palette: string[]): string {
  return palette[0] ?? '';
}

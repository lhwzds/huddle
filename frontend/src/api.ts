import { apiUrl } from './base';
import type {
  AgentProfile,
  AppConfig,
  Conversation,
  Event,
  FileContent,
  FileInfo,
  Group,
  Snapshot,
} from './types';
const base = (id: string) => apiUrl(`api/conversations/${encodeURIComponent(id)}`);

/** An answer that was not a success; it carries the status and the detail. */
export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail || `Request failed (${status})`);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

/** True when the answer says that the caller needs the access cookie. */
export function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

async function detailOf(response: Response): Promise<string> {
  try {
    const body = await response.json();
    const detail = (body as { detail?: unknown } | null)?.detail;
    return typeof detail === 'string' ? detail : '';
  } catch {
    return '';
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) throw new ApiError(response.status, await detailOf(response));
  return response.json() as Promise<T>;
}
const post = <T>(url: string, body?: object) =>
  request<T>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  });
const put = <T>(url: string, body: object) =>
  request<T>(url, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

/* The group routes. Every URL is built under the app's base path. */

/** The configuration of the app: the default group, the limits, the palette. */
export function configUrl(basePath?: string | null): string {
  return apiUrl('api/config', basePath);
}

/** The list of the groups. */
export function groupsUrl(basePath?: string | null): string {
  return apiUrl('api/groups', basePath);
}
/** One group. */
export function groupUrl(group: string, basePath?: string | null): string {
  return apiUrl(`api/groups/${encodeURIComponent(group)}`, basePath);
}
/** The members of a group. */
export function groupAgentsUrl(group: string, basePath?: string | null): string {
  return `${groupUrl(group, basePath)}/agents`;
}
/** One member of a group. */
export function groupAgentUrl(group: string, key: string, basePath?: string | null): string {
  return `${groupAgentsUrl(group, basePath)}/${encodeURIComponent(key)}`;
}
/** Putting a group back the way it was. */
export function groupResetUrl(group: string, basePath?: string | null): string {
  return `${groupUrl(group, basePath)}/reset`;
}

export const api = {
  /** The configuration of the app, read once when it starts. */
  config: () => request<AppConfig>(configUrl()),
  conversations: () => request<Conversation[]>(apiUrl('api/conversations')),
  /** A new conversation, in the group it was asked for. */
  create: (group?: string) =>
    post<Conversation>(apiUrl('api/conversations'), group ? { group } : {}),
  snapshot: (id: string) => request<Snapshot>(base(id)),
  send: (id: string, text: string) => post(base(id) + '/messages', { text }),
  stop: (id: string) => post(base(id) + '/stop'),
  files: (id: string) => request<FileInfo[]>(base(id) + '/files'),
  file: (id: string, path: string) =>
    request<FileContent>(base(id) + '/files/' + path.split('/').map(encodeURIComponent).join('/')),
  /** Every group, with what all of its members know. */
  groups: () => request<Group[]>(groupsUrl()),
  /** The name, the description and the shared context of a group. */
  updateGroup: (group: string, changes: { name?: string; description?: string; common?: string }) =>
    put<Group>(groupUrl(group), changes),
  /** A group back the way it was: the same members, the same context. */
  resetGroup: (group: string) => post<Group>(groupResetUrl(group)),
  /** The members of a group, with the notes only they can see. */
  groupAgents: (group: string) => request<AgentProfile[]>(groupAgentsUrl(group)),
  createGroupAgent: (group: string, profile: Omit<AgentProfile, 'key'>) =>
    post<AgentProfile>(groupAgentsUrl(group), profile),
  updateGroupAgent: (group: string, key: string, changes: Partial<Omit<AgentProfile, 'key'>>) =>
    put<AgentProfile>(groupAgentUrl(group, key), changes),
};
const eventTypes = [
  'message.created',
  'message.delta',
  'message.done',
  'message.tool',
  'agent.typing',
  'agent.context',
  'file.changed',
];
export function connect(
  convId: string,
  afterSeq: number,
  onEvent: (event: Event) => void,
  onGap: () => void,
): () => void {
  let cursor = afterSeq,
    closed = false,
    source: EventSource | undefined,
    timer: ReturnType<typeof setTimeout> | undefined;
  const open = () => {
    if (closed) return;
    source = new EventSource(
      apiUrl(`api/conversations/${encodeURIComponent(convId)}/events?after=${cursor}`),
    );
    for (const type of eventTypes)
      source.addEventListener(type, (raw) => {
        let event: Event;
        try {
          event = JSON.parse((raw as MessageEvent).data) as Event;
        } catch {
          return;
        }
        if (event.seq <= cursor) return;
        if (event.seq > cursor + 1) {
          source?.close();
          closed = true;
          onGap();
          return;
        }
        cursor = event.seq;
        onEvent(event);
      });
    source.onerror = () => {
      source?.close();
      if (!closed) timer = setTimeout(open, 1000);
    };
  };
  open();
  return () => {
    closed = true;
    source?.close();
    if (timer) clearTimeout(timer);
  };
}

export interface Conversation {
  id: string;
  title: string | null;
  created_at: string;
  /** The group the conversation belongs to. */
  group?: string;
}
export interface Message {
  id: string;
  author: string;
  turn: number;
  reply_to: string[];
  text: string;
  status: 'streaming' | 'done' | 'stopped';
  effort: string | null;
  pos: number;
  created_at: string;
}
export interface Agent {
  key: string;
  name: string;
  role: string;
  color: string;
  lens: string;
  typing: boolean;
  context: { used: number; limit: number };
}
/** A member as the people API knows them: identity plus their own notes. */
export interface AgentProfile {
  key: string;
  name: string;
  role: string;
  color: string;
  lens: string;
  soul: string;
  memory: string;
}
/** A group of members and what all of them know. */
export interface Group {
  key: string;
  name: string;
  description: string;
  common: string;
  agent_count: number;
  preset: boolean;
  /** The questions the group opens with, as its config wrote them. */
  examples: string[];
}
/** The group of a conversation, as the conversation knows it. */
export interface GroupRef {
  key: string;
  name: string;
}
/**
 * The limits of the fields the app edits. The backend owns them: every
 * counter and every input reads its limit from here.
 */
export interface Limits {
  /** How many members one group may hold. */
  agents_per_group: number;
  name: number;
  role: number;
  lens: number;
  soul: number;
  memory: number;
  group_name: number;
  group_description: number;
  group_common: number;
  message: number;
}
/** What the app reads from the backend when it starts. */
export interface AppConfig {
  /** The group a conversation belongs to when nobody says otherwise. */
  default_group: string;
  limits: Limits;
  /** The colours the picker offers. */
  palette: string[];
}
export interface Snapshot {
  conversation: Conversation;
  group?: GroupRef;
  last_seq: number;
  messages: Message[];
  agents: Agent[];
}
export type Event = { seq: number; ts: string } & (
  | { type: 'message.created'; data: { message: Message } }
  | { type: 'message.delta'; data: { id: string; text: string } }
  | { type: 'message.done'; data: { id: string; status: 'done' | 'stopped' } }
  | { type: 'message.tool'; data: { id: string; tool: string; summary: string } }
  | { type: 'agent.typing'; data: { agent: string; typing: boolean } }
  | { type: 'agent.context'; data: { agent: string; used: number; limit: number } }
  | { type: 'file.changed'; data: { path: string; size: number; agent: string } }
);
export interface FileInfo {
  path: string;
  size: number;
  mtime: string;
}
export interface FileContent {
  path: string;
  content: string;
  binary: boolean;
}

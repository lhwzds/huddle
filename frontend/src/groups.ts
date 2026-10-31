import type { AgentProfile, Group } from './types';

/**
 * The groups as the picker shows them: the presets first, the rest in the
 * order the server gave them.
 */
export function sortGroups(groups: Group[]): Group[] {
  return [...(groups ?? [])].sort((a, b) => Number(Boolean(b.preset)) - Number(Boolean(a.preset)));
}

/**
 * The example questions of a group: the ones its config wrote, none for a
 * group without any.
 */
export function groupExamples(group: Group | null | undefined): string[] {
  return group?.examples ?? [];
}

/** One member, as a card shows them: just enough for an avatar. */
export interface GroupMember {
  key: string;
  name: string;
  color: string;
}

/** A group, as the picker shows it. */
export interface GroupCard {
  key: string;
  name: string;
  description: string;
  agent_count: number;
  preset: boolean;
  members: GroupMember[];
}

/** The first few members of a group, for the avatars on its card. */
export function cardMembers(agents: AgentProfile[] | undefined, max = 5): GroupMember[] {
  return (agents ?? [])
    .filter((agent) => agent && agent.key)
    .slice(0, max)
    .map((agent) => ({ key: agent.key, name: agent.name || agent.key, color: agent.color }));
}

/**
 * The cards of the picker: the groups in order, each with the members it
 * holds.
 */
export function groupCards(
  groups: Group[],
  members: Record<string, AgentProfile[]> = {},
): GroupCard[] {
  return sortGroups(groups ?? []).map((group) => ({
    key: group.key,
    name: group.name || group.key,
    description: group.description ?? '',
    agent_count: group.agent_count ?? (members[group.key] ?? []).length,
    preset: Boolean(group.preset),
    members: cardMembers(members[group.key]),
  }));
}

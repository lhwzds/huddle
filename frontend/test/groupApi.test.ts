import { describe, expect, it } from 'vitest';
import { api, groupAgentUrl, groupAgentsUrl, groupResetUrl, groupUrl, groupsUrl } from '../src/api';
import apiSource from '../src/api.ts?raw';

describe('the group URLs', () => {
  it('sit at the root when the app is served at the root', () => {
    expect(groupsUrl('/')).toBe('/api/groups');
    expect(groupUrl('family', '/')).toBe('/api/groups/family');
    expect(groupAgentsUrl('family', '/')).toBe('/api/groups/family/agents');
    expect(groupAgentUrl('family', 'mira', '/')).toBe('/api/groups/family/agents/mira');
    expect(groupResetUrl('family', '/')).toBe('/api/groups/family/reset');
  });
  it('carry the base path when the app is served under one', () => {
    expect(groupsUrl('/huddle/')).toBe('/huddle/api/groups');
    expect(groupUrl('family', '/huddle/')).toBe('/huddle/api/groups/family');
    expect(groupAgentsUrl('family', '/huddle/')).toBe('/huddle/api/groups/family/agents');
    expect(groupAgentUrl('family', 'mira', '/huddle/')).toBe(
      '/huddle/api/groups/family/agents/mira',
    );
    expect(groupResetUrl('family', '/huddle/')).toBe('/huddle/api/groups/family/reset');
  });
  it('escape what they carry', () => {
    expect(groupUrl('a b', '/')).toBe('/api/groups/a%20b');
    expect(groupAgentUrl('family', 'the o', '/')).toBe('/api/groups/family/agents/the%20o');
  });
});

describe('the colleague routes', () => {
  it('are the ones of a group now', () => {
    expect(Object.keys(api)).toContain('groupAgents');
    expect(Object.keys(api)).toContain('createGroupAgent');
    expect(Object.keys(api)).toContain('updateGroupAgent');
    expect(Object.keys(api)).toContain('resetGroup');
  });
  it('do not use the old routes any more', () => {
    expect(apiSource).not.toContain('api/agents');
  });
});

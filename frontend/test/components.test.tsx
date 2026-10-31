import { describe, expect, it } from 'vitest';
import { renderToString } from 'react-dom/server';
import AvatarBar from '../src/components/AvatarBar';
import Chat from '../src/components/Chat';
import GroupDrawer from '../src/components/GroupDrawer';
import GroupPicker from '../src/components/GroupPicker';
import ProfileDrawer from '../src/components/ProfileDrawer';
import { applySnapshot } from '../src/state';
import type { Group, Limits, Snapshot } from '../src/types';

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
const palette = ['#3d6b57', '#4b6ea9'];

/** The text of a rendered page, without the marks the renderer leaves in it. */
const text = (html: string) => html.replace(/<!-- -->/g, '');

const group: Group = {
  key: 'startup',
  name: 'Startup',
  description: 'The people who build the thing',
  common: '# Startup\n\nWe are three people.',
  agent_count: 1,
  preset: true,
  examples: ['Should we take the Acme deal?'],
};
const mira = {
  key: 'mira',
  name: 'Mira',
  role: 'CFO',
  color: '#3d6b57',
  lens: 'money',
  soul: '',
  memory: '',
};
const snapshot: Snapshot = {
  conversation: { id: 'c1', title: null, created_at: '2025-01-01T12:00:00Z', group: 'startup' },
  group: { key: 'startup', name: 'Startup' },
  last_seq: 0,
  messages: [],
  agents: [
    {
      key: 'mira',
      name: 'Mira',
      role: 'CFO',
      color: '#3d6b57',
      lens: 'money',
      typing: false,
      context: { used: 10, limit: 24000 },
    },
  ],
};

describe('the group picker', () => {
  it('shows the groups as cards, with their members', () => {
    const html = renderToString(
      <GroupPicker
        groups={[group]}
        members={{ startup: [mira] }}
        onPick={() => {}}
        onClose={() => {}}
      />,
    );
    expect(html).toContain('Startup');
    expect(html).toContain('The people who build the thing');
    expect(html).toContain('member');
    expect(html).toContain('M');
  });
});

describe('the group drawer', () => {
  it('shows the name, the description and the shared context', () => {
    const html = renderToString(
      <GroupDrawer group={group} limits={limits} onClose={() => {}} onSaved={() => {}} />,
    );
    expect(html).toContain('Shared context');
    expect(html).toContain('What everyone in this group knows');
  });
  it('takes every limit from the configuration', () => {
    const html = renderToString(
      <GroupDrawer group={group} limits={limits} onClose={() => {}} onSaved={() => {}} />,
    );
    expect(text(html)).toContain('/ 4,200');
    expect(html).toContain('maxLength="40"');
    expect(html).toContain('maxLength="90"');
    expect(html).toContain('Group name');
  });
});

describe('the profile drawer', () => {
  it('takes the limits and the colours from the configuration', () => {
    const html = renderToString(
      <ProfileDrawer
        agent={null}
        group="startup"
        limits={limits}
        palette={palette}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    expect(html).toContain('maxLength="24"');
    expect(html).toContain('maxLength="32"');
    expect(html).toContain('maxLength="48"');
    expect(text(html)).toContain('0 / 24');
    expect(text(html)).toContain('up to 6 members');
    expect(html).toContain('#3d6b57');
    expect(html).toContain('placeholder="Name"');
  });
});

describe('the empty conversation', () => {
  it('shows the group and the questions it opens with', () => {
    const html = renderToString(
      <Chat
        state={applySnapshot(snapshot)}
        send={async () => {}}
        stop={() => {}}
        openFile={() => {}}
        roster={[
          { key: 'mira', name: 'Mira', role: 'CFO', color: '#3d6b57', typing: false, known: true },
        ]}
        group={{ key: 'startup', name: 'Startup' }}
        examples={['Should we take the Acme deal?']}
        drawerOpen={false}
        limits={limits}
        onOpenProfile={() => {}}
        onAddProfile={() => {}}
        onUseExample={() => {}}
      />,
    );
    expect(html).toContain('Startup');
    expect(html).toContain('Should we take the Acme deal?');
  });
  it('gives the composer the limit of the configuration', () => {
    const html = renderToString(
      <Chat
        state={applySnapshot(snapshot)}
        send={async () => {}}
        stop={() => {}}
        openFile={() => {}}
        roster={[
          { key: 'mira', name: 'Mira', role: 'CFO', color: '#3d6b57', typing: false, known: true },
        ]}
        group={{ key: 'startup', name: 'Startup' }}
        drawerOpen={false}
        limits={limits}
        onOpenProfile={() => {}}
        onAddProfile={() => {}}
        onUseExample={() => {}}
      />,
    );
    expect(html).toContain('maxLength="4000"');
    expect(html).toContain('Message the group');
  });
});

describe('the avatar bar', () => {
  it('shows a member', () => {
    const html = renderToString(
      <AvatarBar
        agents={[
          { key: 'mira', name: 'Mira', role: 'CFO', color: '#3d6b57', typing: false, known: true },
        ]}
        onOpen={() => {}}
        onAdd={() => {}}
      />,
    );
    expect(html).toContain('Mira');
    expect(html).toContain('Add');
  });
});

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api, connect, isUnauthorized } from './api';
import { applyEvent, applySnapshot, mergeAgents, type State } from './state';
import { buildRoster } from './avatarBar';
import { groupExamples } from './groups';
import Chat, { type ExamplePrompt } from './components/Chat';
import FilesPanel from './components/FilesPanel';
import GroupDrawer from './components/GroupDrawer';
import GroupPicker from './components/GroupPicker';
import ProfileDrawer from './components/ProfileDrawer';
import { displayTitle } from './displayTitle';
import { codeFromSearch, stripCodeFromUrl, submitAccessCode } from './access';
import type { AgentProfile, AppConfig, Group } from './types';

type Access = 'open' | 'checking' | 'locked';

export default function App() {
  const { id } = useParams();
  // The router renders this component for both the home page and conversation URLs.
  const navigate = useNavigate();
  const [state, setState] = useState<State | null>(null);
  const [error, setError] = useState(false);
  const [generation, setGeneration] = useState(0);
  // The configuration of the app: the default group, the limits, the palette.
  const [config, setConfig] = useState<AppConfig | null>(null);
  const configLoaded = useRef(false);
  const [groups, setGroups] = useState<Group[]>([]);
  const [members, setMembers] = useState<Record<string, AgentProfile[]>>({});
  const [groupKey, setGroupKey] = useState<string | null>(null);
  const [groupOpen, setGroupOpen] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [profileKey, setProfileKey] = useState<string | null>(null);
  const [addingAgent, setAddingAgent] = useState(false);
  const [filesOpen, setFilesOpen] = useState(false);
  const [fileDot, setFileDot] = useState(false);
  const [fileCount, setFileCount] = useState(0);
  const [path, setPath] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [prefill, setPrefill] = useState<ExamplePrompt | null>(null);
  // An invite link carries the code; it is exchanged for the cookie once.
  const code = useMemo(() => codeFromSearch(window.location.search), []);
  const [access, setAccess] = useState<Access>(code ? 'checking' : 'open');
  const locked = useCallback(() => setAccess('locked'), []);
  useEffect(() => {
    if (access !== 'checking' || !code) return;
    let alive = true;
    void submitAccessCode(code).then((ok) => {
      if (!alive) return;
      if (ok) {
        stripCodeFromUrl(window.history, window.location);
        setAccess('open');
        setGeneration((x) => x + 1);
      } else setAccess('locked');
    });
    return () => {
      alive = false;
    };
  }, [access, code]);
  // The configuration comes once, when the app starts: every field of the
  // app takes its limit and its colours from it.
  useEffect(() => {
    if (access !== 'open' || configLoaded.current) return;
    let alive = true;
    api
      .config()
      .then((c) => {
        if (!alive) return;
        configLoaded.current = true;
        setConfig(c);
      })
      .catch((err) => {
        if (!alive) return;
        if (isUnauthorized(err)) locked();
        else setError(true);
      });
    return () => {
      alive = false;
    };
  }, [access, generation, locked]);
  useEffect(() => {
    if (id || access !== 'open') return;
    let alive = true;
    api
      .conversations()
      .then(async (list) => list[0] ?? (await api.create()))
      .then((c) => {
        if (alive) navigate(`/c/${c.id}`, { replace: true });
      })
      .catch((err) => {
        if (!alive) return;
        if (isUnauthorized(err)) locked();
        else setError(true);
      });
    return () => {
      alive = false;
    };
  }, [id, navigate, generation, access, locked]);
  // The groups are the same for every conversation: they are fetched once.
  useEffect(() => {
    if (access !== 'open') return;
    let alive = true;
    api
      .groups()
      .then((list) => {
        if (alive) setGroups(list);
      })
      .catch(() => {
        /* the group falls back to what the conversation knows */
      });
    return () => {
      alive = false;
    };
  }, [access, generation]);
  useEffect(() => {
    if (!id || access !== 'open' || !config) return;
    let alive = true;
    let disconnect: (() => void) | undefined;
    setState(null);
    setError(false);
    setPath(null);
    api
      .snapshot(id)
      .then((snapshot) => {
        if (!alive) return;
        const next = applySnapshot(snapshot);
        setState(next);
        setGroupKey(next.group?.key ?? snapshot.conversation.group ?? config.default_group);
        disconnect = connect(
          id,
          snapshot.last_seq,
          (event) => {
            if (alive) setState((current) => (current ? applyEvent(current, event) : current));
          },
          () => {
            if (alive) setGeneration((x) => x + 1);
          },
        );
      })
      .catch((err) => {
        if (!alive) return;
        if (isUnauthorized(err)) locked();
        else setError(true);
      });
    return () => {
      alive = false;
      disconnect?.();
    };
  }, [id, generation, access, locked, config]);
  // The members of the group this conversation belongs to.
  useEffect(() => {
    if (!groupKey || access !== 'open') return;
    let alive = true;
    api
      .groupAgents(groupKey)
      .then((list) => {
        if (!alive) return;
        setMembers((current) => ({ ...current, [groupKey]: list }));
      })
      .catch(() => {
        /* the roster falls back to what the conversation knows */
      });
    return () => {
      alive = false;
    };
  }, [groupKey, access, generation]);
  // A member we don't know yet (someone else added one) is looked up once.
  const people = members[groupKey ?? ''] ?? [];
  const known = useMemo(() => new Set(people.map((p) => p.key)), [people]);
  const looked = useRef(new Set<string>());
  useEffect(() => {
    if (!state || !groupKey || !people.length) return;
    const missing = new Set<string>();
    for (const agent of state.agents) if (!known.has(agent.key)) missing.add(agent.key);
    for (const message of state.messages)
      if (message.author !== 'user' && !known.has(message.author)) missing.add(message.author);
    const fresh = [...missing].filter((key) => !looked.current.has(key));
    if (!fresh.length) return;
    for (const key of fresh) looked.current.add(key);
    void api
      .groupAgents(groupKey)
      .then((list) => setMembers((current) => ({ ...current, [groupKey]: list })))
      .catch(() => {
        /* nothing else to try */
      });
  }, [state, known, groupKey]);
  // The group of this conversation, as the picker and the drawer know it.
  const currentGroup = useMemo<Group | null>(() => {
    if (!groupKey) return null;
    const found = groups.find((g) => g.key === groupKey);
    if (found) return found;
    return {
      key: groupKey,
      name: state?.group?.name ?? groupKey,
      description: '',
      common: '',
      agent_count: people.length,
      preset: false,
      examples: [],
    };
  }, [groupKey, groups, state, people.length]);
  // The examples this group opens with: the ones its config wrote.
  const examples = useMemo(() => groupExamples(currentGroup), [currentGroup]);
  const openPicker = useCallback(() => {
    setPickerOpen(true);
    void api
      .groups()
      .then((list) => {
        setGroups(list);
        return Promise.all(
          list.map(async (group) => {
            try {
              return [group.key, await api.groupAgents(group.key)] as const;
            } catch {
              return [group.key, [] as AgentProfile[]] as const;
            }
          }),
        );
      })
      .then((entries) => setMembers((current) => ({ ...current, ...Object.fromEntries(entries) })))
      .catch(() => {
        /* the picker shows the groups it knows */
      });
  }, []);
  const startChat = async (group: string) => {
    if (creating) return;
    setCreating(true);
    try {
      const c = await api.create(group);
      setPickerOpen(false);
      navigate(`/c/${c.id}`);
      setFilesOpen(false);
    } catch (err) {
      if (isUnauthorized(err)) locked();
      else setError(true);
    } finally {
      setCreating(false);
    }
  };
  const send = useCallback(
    async (text: string) => {
      if (!id) return;
      try {
        await api.send(id, text);
      } catch (err) {
        if (isUnauthorized(err)) locked();
        throw err;
      }
    },
    [id, locked],
  );
  const stop = useCallback(() => {
    if (!id) return;
    void api.stop(id).catch((err) => {
      if (isUnauthorized(err)) locked();
    });
  }, [id, locked]);
  const openFile = (file: string) => {
    setPath(file);
    setFilesOpen(true);
    setFileDot(false);
  };
  // The people the avatar bar shows: the known members and their state.
  const roster = useMemo(() => {
    const agents = state?.agents ?? [];
    const messages = state?.messages ?? [];
    const source = people.length
      ? people.map((p) => ({ key: p.key, name: p.name, role: p.role, color: p.color }))
      : agents.map((a) => ({ key: a.key, name: a.name, role: a.role, color: a.color }));
    return buildRoster(source, agents, messages);
  }, [people, state]);
  const selected = useMemo<AgentProfile | null>(() => {
    if (addingAgent || !profileKey) return null;
    const found = people.find((p) => p.key === profileKey);
    if (found) return found;
    const entry = roster.find((r) => r.key === profileKey);
    if (!entry) return null;
    return {
      key: entry.key,
      name: entry.name,
      role: entry.role,
      color: entry.color,
      lens: '',
      soul: '',
      memory: '',
    };
  }, [addingAgent, profileKey, people, roster]);
  const drawerOpen = addingAgent || profileKey !== null || groupOpen;
  // After a change, the members and the conversation's view of them are fresh.
  const refreshPeople = useCallback(async () => {
    if (!groupKey) return;
    try {
      const list = await api.groupAgents(groupKey);
      setMembers((current) => ({ ...current, [groupKey]: list }));
    } catch {
      /* the list stays as it was */
    }
    try {
      // The group itself may have changed too: its size, its shared context.
      const list = await api.groups();
      setGroups(list);
    } catch {
      /* the group stays as it was */
    }
    if (!id) return;
    try {
      const snapshot = await api.snapshot(id);
      setState((current) => (current ? mergeAgents(current, snapshot.agents) : current));
    } catch (err) {
      if (isUnauthorized(err)) locked();
    }
  }, [groupKey, id, locked]);
  // The group of the conversation, saved.
  const saveGroup = useCallback((updated: Group) => {
    setGroups((current) =>
      current.map((group) =>
        group.key === updated.key
          ? { ...group, ...updated, examples: updated.examples ?? group.examples }
          : group,
      ),
    );
  }, []);
  // A new conversation starts with an empty composer.
  useEffect(() => {
    setPrefill(null);
  }, [id]);
  const context = state?.agents.find((a) => a.key === profileKey)?.context;
  useEffect(() => {
    if (!id) return;
    let alive = true;
    api
      .files(id)
      .then((list) => {
        if (alive) setFileCount(list.length);
      })
      .catch(() => {
        if (alive) setFileCount(0);
      });
    return () => {
      alive = false;
    };
  }, [id, state?.filesVersion]);
  useEffect(() => {
    if (!state?.filesVersion || filesOpen) return;
    setFileDot(true);
  }, [state?.filesVersion, filesOpen]);
  useEffect(() => {
    if (!filesOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !drawerOpen) setFilesOpen(false);
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [filesOpen, drawerOpen]);
  if (access === 'locked') {
    return (
      <div className="access-screen">
        <div className="access-card">
          <div className="brand-mark">✳</div>
          <h1>
            huddle<span className="brand-dot">.</span>
          </h1>
          <p>This Huddle is invite-only. Open the link you were sent.</p>
        </div>
      </div>
    );
  }
  const header = (
    <header className="topbar">
      <div className="brand">
        <div className="brand-mark">✳</div>
        <span>
          huddle<span className="brand-dot">.</span>
        </span>
      </div>
      <div className="header-divider" />
      <div className="conversation-heading">
        <span className="header-label">CONVERSATION</span>
        <strong>{state ? displayTitle(state.conversation, state.messages) : 'New chat'}</strong>
        {currentGroup && (
          <button
            className="group-pill"
            onClick={() => setGroupOpen(true)}
            title={`${currentGroup.name} — the group of this chat`}
          >
            {currentGroup.name}
          </button>
        )}
        <span className="heading-chevron">⌄</span>
      </div>
      <div className="header-actions">
        <button
          className="files-button"
          onClick={() => {
            setFilesOpen(true);
            setFileDot(false);
          }}
          aria-label="Open files"
          aria-expanded={filesOpen}
        >
          <span className="files-icon">▤</span>
          <span className="files-label">Files</span>
          {fileCount > 0 && <span className="files-badge">{fileCount}</span>}
          {fileDot && <span className="files-dot" aria-label="New files" />}
        </button>
        <button className="new-chat" onClick={openPicker} disabled={creating}>
          <span>＋</span> New chat
        </button>
        <div className="user-avatar" title="You">
          Y
        </div>
      </div>
    </header>
  );
  const loading = (
    <div className="loading-screen">
      {error ? (
        <>
          <h2>We couldn't open your huddle.</h2>
          <p>Please check your connection and try again.</p>
          <button
            className="new-chat"
            onClick={() => {
              setError(false);
              setGeneration((x) => x + 1);
            }}
          >
            Try again
          </button>
        </>
      ) : (
        <>
          <div className="welcome-icon">✳</div>
          <p>Getting your group together…</p>
        </>
      )}
    </div>
  );
  if (!config) {
    return (
      <div className="app-shell">
        {header}
        {loading}
      </div>
    );
  }
  return (
    <div className="app-shell">
      {header}
      {state && id ? (
        <main className="workspace">
          <Chat
            key={id}
            state={state}
            send={send}
            stop={stop}
            openFile={openFile}
            roster={roster}
            group={state.group}
            examples={examples}
            prefill={prefill && prefill.conv === id ? prefill : null}
            drawerOpen={drawerOpen || filesOpen}
            limits={config.limits}
            onOpenProfile={(key) => {
              setProfileKey(key);
              setAddingAgent(false);
            }}
            onAddProfile={() => {
              setProfileKey(null);
              setAddingAgent(true);
            }}
            onUseExample={(text) =>
              setPrefill((current) => ({
                text,
                nonce: (current?.nonce ?? 0) + 1,
                conv: id,
              }))
            }
          />
          <aside className={`drawer files-drawer${filesOpen ? ' open' : ''}`}>
            <div className="drawer-head">
              <div className="drawer-title">
                FILES <span>✳</span>
              </div>
              <button
                className="drawer-close"
                onClick={() => setFilesOpen(false)}
                aria-label="Close files"
              >
                ×
              </button>
            </div>
            <div className="drawer-body">
              <FilesPanel id={id} version={state.filesVersion} path={path} setPath={setPath} />
            </div>
          </aside>
          {filesOpen && (
            <button
              className="drawer-backdrop"
              aria-label="Close files"
              onClick={() => setFilesOpen(false)}
            />
          )}
          {drawerOpen && groupOpen && currentGroup && (
            <GroupDrawer
              group={currentGroup}
              limits={config.limits}
              onClose={() => setGroupOpen(false)}
              onSaved={saveGroup}
            />
          )}
          {drawerOpen && !groupOpen && (
            <ProfileDrawer
              key={addingAgent ? 'new' : profileKey}
              agent={selected}
              group={groupKey ?? config.default_group}
              groupName={currentGroup?.name}
              context={context}
              limits={config.limits}
              palette={config.palette}
              onClose={() => {
                setProfileKey(null);
                setAddingAgent(false);
              }}
              onChanged={refreshPeople}
            />
          )}
        </main>
      ) : (
        loading
      )}
      {pickerOpen && (
        <GroupPicker
          groups={groups}
          members={members}
          busy={creating}
          onPick={(group) => void startChat(group)}
          onClose={() => setPickerOpen(false)}
        />
      )}
    </div>
  );
}

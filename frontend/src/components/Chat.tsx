import { Fragment, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { nextFollow } from '../follow';
import type { State } from '../state';
import type { RosterAgent } from '../avatarBar';
import type { GroupRef, Limits } from '../types';
import AvatarBar from './AvatarBar';
import Composer from './Composer';
import MessageBubble, { Avatar } from './MessageBubble';
import TurnDivider from './TurnDivider';

/** A question to start with: it fills the composer, it does not send itself. */
export interface ExamplePrompt {
  text: string;
  nonce: number;
  /** The conversation it belongs to. */
  conv?: string | null;
}

export default function Chat({
  state,
  send,
  stop,
  openFile,
  roster,
  group,
  examples,
  prefill,
  drawerOpen,
  limits,
  onOpenProfile,
  onAddProfile,
  onUseExample,
}: {
  state: State;
  send: (text: string) => Promise<void>;
  stop: () => void;
  openFile: (path: string) => void;
  roster: RosterAgent[];
  group?: GroupRef | null;
  examples?: string[];
  prefill?: ExamplePrompt | null;
  drawerOpen: boolean;
  /** The limits of the fields, as the configuration says them. */
  limits: Limits;
  onOpenProfile: (key: string) => void;
  onAddProfile: () => void;
  onUseExample: (text: string) => void;
}) {
  const scroll = useRef<HTMLDivElement>(null);
  const near = useRef(true);
  const userAt = useRef(0);
  const markUser = () => {
    userAt.current = Date.now();
  };
  const [unseen, setUnseen] = useState(false);
  const active =
    state.agents.some((a) => a.typing) || state.messages.some((m) => m.status === 'streaming');
  const waiting = state.agents.filter(
    (a) => a.typing && !state.messages.some((m) => m.author === a.key && m.status === 'streaming'),
  );
  const names = waiting.map((a) => a.name);
  const typing =
    names.length === 1
      ? `${names[0]} is`
      : names.length === 2
        ? `${names[0]} and ${names[1]} are`
        : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]} are`;
  useLayoutEffect(() => {
    const el = scroll.current;
    if (!el) return;
    if (near.current) {
      el.scrollTop = el.scrollHeight;
      setUnseen(false);
    } else setUnseen(true);
  }, [state.messages, state.agents]);
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && active && !drawerOpen && !(e.target instanceof HTMLTextAreaElement))
        stop();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [active, stop, drawerOpen]);
  return (
    <section className="chat">
      <AvatarBar agents={roster} onOpen={onOpenProfile} onAdd={onAddProfile} />
      <div
        className="chat-scroll"
        ref={scroll}
        onWheel={markUser}
        onTouchMove={markUser}
        onKeyDown={markUser}
        onScroll={(e) => {
          const el = e.currentTarget;
          const byUser = Date.now() - userAt.current < 800;
          near.current = nextFollow(
            near.current,
            el.scrollHeight - el.scrollTop - el.clientHeight,
            byUser,
          );
          if (near.current) setUnseen(false);
        }}
      >
        <div className="chat-content">
          {state.messages.length === 0 ? (
            <div className="welcome">
              <div className="welcome-icon">✳</div>
              {group && <div className="welcome-group">GROUP · {group.name}</div>}
              <div className="eyebrow">YOUR SPACE TO THINK TOGETHER</div>
              <h1>
                Good ideas start
                <br />
                <em>with a huddle.</em>
              </h1>
              <p>
                Bring a question, a half-formed idea, or a big decision. The group is here to think
                it through with you.
              </p>
              <div className="welcome-people">
                {state.agents.map((a) => (
                  <Avatar key={a.key} agent={a} size="small" />
                ))}
                <span>
                  Meet the group <b>↗</b>
                </span>
              </div>
              {examples && examples.length > 0 && (
                <div className="prompt-cards">
                  {examples.map((text, i) => (
                    <button
                      key={text}
                      type="button"
                      className="prompt-card"
                      onClick={() => onUseExample(text)}
                    >
                      <span>{String(i + 1).padStart(2, '0')}</span>
                      <p>{text}</p>
                      <b>↗</b>
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : (
            state.messages.map((m) => (
              <Fragment key={m.id}>
                {m.author === 'user' && <TurnDivider turn={m.turn} time={m.created_at} />}
                <MessageBubble message={m} agents={roster} openFile={openFile} />
              </Fragment>
            ))
          )}
          {waiting.length > 0 && (
            <div className="typing-line">
              <span className="typing-dots">
                <i />
                <i />
                <i />
              </span>
              {typing} typing…
            </div>
          )}
        </div>
      </div>
      {unseen && (
        <button
          className="new-messages"
          onClick={() => {
            near.current = true;
            if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight;
            near.current = true;
            setUnseen(false);
          }}
        >
          New messages ↓
        </button>
      )}
      <Composer
        agents={state.agents}
        active={active}
        send={send}
        stop={stop}
        prefill={prefill}
        limits={limits}
      />
    </section>
  );
}

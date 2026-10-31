import { useEffect, useRef, useState } from 'react';
import type { Agent, Limits } from '../types';
import { ApiError } from '../api';
import { limitOf } from '../config';
import { filterMentions, insertMention, mentionAt } from '../mentions';
import { limitNote } from '../limits';
import { countChars } from '../profile';
import { Avatar } from './MessageBubble';
export default function Composer({
  agents,
  active,
  send,
  stop,
  prefill,
  limits,
}: {
  agents: Agent[];
  active: boolean;
  send: (text: string) => Promise<void>;
  stop: () => void;
  prefill?: { text: string; nonce: number } | null;
  /** The limits of the fields, as the configuration says them. */
  limits: Limits;
}) {
  const [text, setText] = useState('');
  const [cursor, setCursor] = useState(0);
  const [selected, setSelected] = useState(0);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  // An example question fills the composer; it never sends itself.
  useEffect(() => {
    if (!prefill) return;
    setText(prefill.text);
    setCursor(prefill.text.length);
    setSelected(0);
    const el = input.current;
    if (el) {
      el.focus();
      el.setSelectionRange(prefill.text.length, prefill.text.length);
    }
  }, [prefill?.nonce]);
  const mention = mentionAt(text, cursor);
  const options = mention ? filterMentions(agents, mention.query) : [];
  const choose = (name: string) => {
    if (!mention) return;
    const result = insertMention(text, mention, name);
    setText(result.text);
    setCursor(result.cursor);
    setSelected(0);
    requestAnimationFrame(() => {
      input.current?.focus();
      input.current?.setSelectionRange(result.cursor, result.cursor);
    });
  };
  const submit = async () => {
    if (!text.trim() || sending) return;
    const submitted = text;
    setSending(true);
    setError(false);
    setNotice(null);
    try {
      await send(submitted);
      setText((current) => (current === submitted ? '' : current));
      setCursor(0);
    } catch (err) {
      // The text stays in the composer: the message can be sent again.
      const note = limitNote(err instanceof ApiError ? err.detail : '');
      if (note) setNotice(note);
      else setError(true);
    } finally {
      setSending(false);
      input.current?.focus();
    }
  };
  const count = countChars(text);
  return (
    <div className="composer-wrap">
      <div className="composer-inner">
        {options.length > 0 && (
          <div className="mention-menu" role="listbox" aria-label="Mention a member">
            {options.map((a, i) => (
              <button
                type="button"
                role="option"
                aria-selected={i === selected % options.length}
                className={i === selected % options.length ? 'selected' : ''}
                key={a.key}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => choose(a.name)}
              >
                <Avatar agent={a} size="tiny" />
                <span>
                  <strong>{a.name}</strong>
                  <small>{a.role}</small>
                </span>
              </button>
            ))}
          </div>
        )}
        {notice && (
          <div className="composer-notice" role="status">
            {notice}
          </div>
        )}
        <div className="composer">
          <textarea
            ref={input}
            aria-label="Message the group"
            placeholder="Message the group…"
            rows={2}
            maxLength={limitOf(limits, 'message')}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setCursor(e.currentTarget.selectionStart);
              setSelected(0);
            }}
            onClick={(e) => setCursor(e.currentTarget.selectionStart)}
            onKeyUp={(e) => setCursor(e.currentTarget.selectionStart)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                if (active) {
                  e.preventDefault();
                  stop();
                }
                return;
              }
              if (options.length && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
                e.preventDefault();
                setSelected(
                  (selected + (e.key === 'ArrowDown' ? 1 : options.length - 1)) % options.length,
                );
                return;
              }
              if (options.length && (e.key === 'Enter' || e.key === 'Tab') && !e.shiftKey) {
                e.preventDefault();
                choose(options[selected % options.length].name);
                return;
              }
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                void submit();
              }
            }}
          />
          <div className="composer-actions">
            <span className="composer-hint">
              Enter to send <span>·</span> Shift + Enter for a new line
            </span>
            <div className="action-buttons">
              {count > 0 && (
                <span className="composer-count">
                  {count.toLocaleString()} / {limitOf(limits, 'message').toLocaleString()}
                </span>
              )}
              {active && (
                <button type="button" className="stop-button" onClick={stop}>
                  <span className="stop-icon" /> Stop
                </button>
              )}
              <button
                type="button"
                className="send-button"
                onClick={() => void submit()}
                disabled={!text.trim() || sending}
              >
                Send <span>↑</span>
              </button>
            </div>
          </div>
        </div>
        {error && <div className="send-error">Couldn't send — retry</div>}
        <div className="composer-footnote">A little more perspective, together.</div>
      </div>
    </div>
  );
}

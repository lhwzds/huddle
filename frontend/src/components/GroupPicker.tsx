import { useEffect } from 'react';
import { avatarInitial } from '../avatarBar';
import { groupCards } from '../groups';
import type { AgentProfile, Group } from '../types';

/**
 * The sheet of a new chat: the groups to start a conversation with, each as a
 * card with the members it holds.
 */
export default function GroupPicker({
  groups,
  members,
  onPick,
  onClose,
  busy,
}: {
  groups: Group[];
  members: Record<string, AgentProfile[]>;
  onPick: (group: string) => void;
  onClose: () => void;
  busy?: boolean;
}) {
  const cards = groupCards(groups, members);
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);
  return (
    <div className="picker-backdrop" onClick={onClose}>
      <div
        className="picker-sheet"
        role="dialog"
        aria-modal="true"
        aria-label="Choose a group"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="picker-head">
          <div>
            <span className="field-label">NEW CHAT</span>
            <h2>Who are you thinking with?</h2>
          </div>
          <button className="drawer-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <div className="picker-body">
          {cards.length === 0 && <p className="picker-empty">No groups yet.</p>}
          {cards.map((card) => (
            <button
              key={card.key}
              className="group-card"
              onClick={() => onPick(card.key)}
              disabled={busy}
            >
              <span className="group-card-avatars" aria-hidden="true">
                {card.members.length ? (
                  card.members.map((member) => (
                    <span
                      key={member.key}
                      className="avatar group-card-avatar"
                      style={{ backgroundColor: member.color }}
                    >
                      {avatarInitial(member.name)}
                    </span>
                  ))
                ) : (
                  <span className="avatar group-card-avatar empty">✳</span>
                )}
              </span>
              <span className="group-card-text">
                <strong>{card.name}</strong>
                <small>{card.description}</small>
                <span className="group-card-count">
                  {card.agent_count} {card.agent_count === 1 ? 'member' : 'members'}
                </span>
              </span>
              <span className="group-card-go" aria-hidden="true">
                ↗
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

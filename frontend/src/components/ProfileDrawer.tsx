import { useEffect, useRef, useState } from 'react';
import { api, ApiError } from '../api';
import { firstColor, limitOf } from '../config';
import {
  contextLabel,
  draftOf,
  hasChanges,
  limitNote,
  limitState,
  normalizeHex,
  profileDiff,
  type ProfileDraft,
} from '../profile';
import type { AgentProfile, Limits } from '../types';
import FieldCount from './FieldCount';
import MarkdownField from './MarkdownField';

const SOUL_HINT = 'Who they are: voice, temperament, how they think.';
const MEMORY_HINT =
  "What only they know and what they've been asked to remember. The others can't see it.";

/** The answer of the server, said calmly. */
function calmNote(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 409)
      return `The group is full for now — ${error.detail || 'too many members'}.`;
    if (error.detail) return error.detail;
  }
  return 'Something went wrong — try again in a moment.';
}

export default function ProfileDrawer({
  agent,
  group,
  groupName,
  context,
  limits,
  palette,
  onClose,
  onChanged,
}: {
  agent: AgentProfile | null;
  /** The group the member belongs to. */
  group: string;
  groupName?: string;
  context?: { used: number; limit: number };
  /** The limits of the fields, as the configuration says them. */
  limits: Limits;
  /** The colours the picker offers. */
  palette: string[];
  onClose: () => void;
  onChanged: () => void | Promise<void>;
}) {
  const color = firstColor(palette);
  const [profile, setProfile] = useState<AgentProfile | null>(agent);
  const [original, setOriginal] = useState<ProfileDraft>(draftOf(agent, color));
  const [draft, setDraft] = useState<ProfileDraft>(draftOf(agent, color));
  const [hex, setHex] = useState(draftOf(agent, color).color);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const creating = !profile;
  const dirty = hasChanges(original, draft);
  const ready = creating ? draft.name.trim().length > 0 : dirty;
  const tooLong =
    !limitState(draft.soul, limitOf(limits, 'soul')).ok ||
    !limitState(draft.memory, limitOf(limits, 'memory')).ok;
  // The calm sentence about a text that is too long, when there is one.
  const overNote =
    limitNote(draft.soul, limitOf(limits, 'soul')) ||
    limitNote(draft.memory, limitOf(limits, 'memory'));

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const close = () => {
    if (dirty && !window.confirm('Discard changes?')) return;
    onClose();
  };
  const closeRef = useRef(close);
  closeRef.current = close;
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeRef.current();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  const update = (field: keyof ProfileDraft, value: string) => {
    setDraft((current) => ({ ...current, [field]: value }));
    if (field === 'color') setHex(value);
    setError(null);
  };
  const flash = () => {
    setSaved(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setSaved(false), 2000);
  };
  const save = async () => {
    if (busy || !ready || tooLong) return;
    setBusy(true);
    setError(null);
    try {
      if (creating) {
        const created = await api.createGroupAgent(group, {
          name: draft.name.trim(),
          role: draft.role.trim(),
          color: draft.color,
          lens: draft.lens.trim(),
          soul: draft.soul,
          memory: draft.memory,
        });
        setProfile(created);
        setOriginal(draftOf(created, color));
        setDraft(draftOf(created, color));
        setHex(created.color);
        flash();
        await onChanged();
      } else {
        const changes = profileDiff(original, draft);
        if (!Object.keys(changes).length) return;
        const updated = await api.updateGroupAgent(group, profile.key, changes);
        setProfile(updated);
        setOriginal(draftOf(updated, color));
        setDraft(draftOf(updated, color));
        setHex(updated.color);
        flash();
        await onChanged();
      }
    } catch (err) {
      setError(calmNote(err));
    } finally {
      setBusy(false);
    }
  };
  const reset = async () => {
    if (busy) return;
    if (!window.confirm('Reset this group to its defaults? Every change is lost.')) return;
    setBusy(true);
    setError(null);
    try {
      await api.resetGroup(group);
      await onChanged();
      onClose();
    } catch (err) {
      setError(calmNote(err));
      setBusy(false);
    }
  };
  const initial = (draft.name || '?').charAt(0).toUpperCase();
  return (
    <>
      <button className="drawer-backdrop" aria-label="Close" onClick={close} />
      <aside
        className="drawer profile-drawer open"
        role="dialog"
        aria-modal="true"
        aria-label={creating ? 'Add a member' : `Profile of ${profile?.name ?? ''}`}
      >
        <div className="drawer-head">
          <button className="drawer-close" onClick={close} aria-label="Close">
            ×
          </button>
          <div className="profile-head">
            <span
              className="avatar profile-avatar"
              style={{ backgroundColor: draft.color }}
              aria-hidden="true"
            >
              {initial}
            </span>
            <div className="profile-head-text">
              <h2>{draft.name || 'New member'}</h2>
              <p>{draft.role || 'What do they bring?'}</p>
              {groupName && <p className="profile-group">in {groupName}</p>}
            </div>
          </div>
        </div>
        <div className="drawer-body">
          <div className="field-block">
            <div className="field-head">
              <span className="field-label">Name</span>
              <FieldCount value={draft.name} max={limitOf(limits, 'name')} />
            </div>
            <input
              className="field-input"
              value={draft.name}
              maxLength={limitOf(limits, 'name')}
              placeholder="Name"
              onChange={(e) => update('name', e.target.value)}
            />
          </div>
          <div className="field-block">
            <div className="field-head">
              <span className="field-label">Role</span>
              <FieldCount value={draft.role} max={limitOf(limits, 'role')} />
            </div>
            <input
              className="field-input"
              value={draft.role}
              maxLength={limitOf(limits, 'role')}
              placeholder="Role"
              onChange={(e) => update('role', e.target.value)}
            />
          </div>
          <div className="field-block">
            <div className="field-head">
              <span className="field-label">Lens</span>
              <FieldCount value={draft.lens} max={limitOf(limits, 'lens')} />
            </div>
            <input
              className="field-input"
              value={draft.lens}
              maxLength={limitOf(limits, 'lens')}
              placeholder="The angle they bring to every question"
              onChange={(e) => update('lens', e.target.value)}
            />
          </div>
          <div className="field-block">
            <div className="field-head">
              <span className="field-label">Colour</span>
            </div>
            <div className="color-row">
              {palette.map((swatch) => (
                <button
                  key={swatch}
                  type="button"
                  className={`swatch${
                    draft.color.toLowerCase() === swatch.toLowerCase() ? ' active' : ''
                  }`}
                  style={{ backgroundColor: swatch }}
                  aria-label={`Colour ${swatch}`}
                  onClick={() => update('color', swatch)}
                />
              ))}
              <span className="color-custom">
                <input
                  className="hex-input"
                  value={hex}
                  aria-label="Custom colour"
                  placeholder="#123456"
                  onChange={(e) => {
                    const raw = e.target.value;
                    setHex(raw);
                    const normalized = normalizeHex(raw);
                    if (normalized) setDraft((current) => ({ ...current, color: normalized }));
                  }}
                />
                <input
                  type="color"
                  className="color-input"
                  value={normalizeHex(hex) || draft.color}
                  aria-label="Pick a colour"
                  onChange={(e) => update('color', e.target.value)}
                />
              </span>
            </div>
          </div>
          <MarkdownField
            label="Soul"
            hint={SOUL_HINT}
            value={draft.soul}
            max={limitOf(limits, 'soul')}
            placeholder="Who they are, in their own words..."
            onChange={(value) => update('soul', value)}
          />
          <MarkdownField
            label="Memory"
            hint={MEMORY_HINT}
            value={draft.memory}
            max={limitOf(limits, 'memory')}
            placeholder="What only they know..."
            onChange={(value) => update('memory', value)}
          />
          {profile && context && (
            <div className="field-block context-block">
              <div className="field-head">
                <span className="field-label">Context</span>
              </div>
              <div className="context-label">
                <span>{contextLabel(context.used, context.limit)}</span>
              </div>
              <div className="context-bar">
                <div
                  style={{
                    width: `${
                      context.limit ? Math.min(100, (context.used / context.limit) * 100) : 0
                    }%`,
                  }}
                />
              </div>
            </div>
          )}
          <p className="field-hint">This group holds up to {limits.agents_per_group} members.</p>
        </div>
        <div className="drawer-foot">
          {error && <div className="form-note">{error}</div>}
          {!error && overNote && <div className="form-note">{overNote}</div>}
          <div className="drawer-actions">
            <button
              className="drawer-save"
              onClick={() => void save()}
              disabled={busy || tooLong || (!ready && !saved)}
            >
              {saved ? 'Saved' : creating ? 'Add to the group' : 'Save'}
            </button>
          </div>
          <button className="reset-link" onClick={() => void reset()} disabled={busy}>
            Reset this group to its defaults
          </button>
        </div>
      </aside>
    </>
  );
}

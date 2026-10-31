import { useEffect, useRef, useState } from 'react';
import { api, ApiError } from '../api';
import { limitOf } from '../config';
import { countChars } from '../profile';
import type { Group, Limits } from '../types';
import FieldCount from './FieldCount';
import MarkdownField from './MarkdownField';

const CONTEXT_HINT =
  "What everyone in this group knows. Private facts belong in each member's Memory.";

/** The answer of the server, said calmly. */
function calmNote(error: unknown): string {
  if (error instanceof ApiError && error.detail) return error.detail;
  return 'Something went wrong — try again in a moment.';
}

/**
 * The group of the conversation: its name, what it is for, and what everyone
 * in it knows.
 */
export default function GroupDrawer({
  group,
  limits,
  onClose,
  onSaved,
}: {
  group: Group;
  /** The limits of the fields, as the configuration says them. */
  limits: Limits;
  onClose: () => void;
  onSaved: (group: Group) => void | Promise<void>;
}) {
  const [name, setName] = useState(group.name ?? '');
  const [description, setDescription] = useState(group.description ?? '');
  const [common, setCommon] = useState(group.common ?? '');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const dirty =
    name.trim() !== (group.name ?? '').trim() ||
    description.trim() !== (group.description ?? '').trim() ||
    common !== (group.common ?? '');
  const ready = name.trim().length > 0 && countChars(common) <= limitOf(limits, 'group_common');

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

  const save = async () => {
    if (busy || !ready) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await api.updateGroup(group.key, {
        name: name.trim(),
        description: description.trim(),
        common,
      });
      setSaved(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setSaved(false), 2000);
      await onSaved(updated);
    } catch (err) {
      setError(calmNote(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <button className="drawer-backdrop" aria-label="Close" onClick={close} />
      <aside
        className="drawer group-drawer open"
        role="dialog"
        aria-modal="true"
        aria-label={`The ${group.name} group`}
      >
        <div className="drawer-head">
          <div className="drawer-title">
            GROUP <span>✳</span>
          </div>
          <button className="drawer-close" onClick={close} aria-label="Close">
            ×
          </button>
        </div>
        <div className="drawer-body">
          <div className="field-block">
            <div className="field-head">
              <span className="field-label">Name</span>
              <FieldCount value={name} max={limitOf(limits, 'group_name')} />
            </div>
            <input
              className="field-input"
              value={name}
              maxLength={limitOf(limits, 'group_name')}
              placeholder="Group name"
              onChange={(e) => {
                setName(e.target.value);
                setError(null);
              }}
            />
          </div>
          <div className="field-block">
            <div className="field-head">
              <span className="field-label">Description</span>
              <FieldCount value={description} max={limitOf(limits, 'group_description')} />
            </div>
            <input
              className="field-input"
              value={description}
              maxLength={limitOf(limits, 'group_description')}
              placeholder="What this group is for"
              onChange={(e) => {
                setDescription(e.target.value);
                setError(null);
              }}
            />
          </div>
          <MarkdownField
            label="Shared context"
            hint={CONTEXT_HINT}
            value={common}
            max={limitOf(limits, 'group_common')}
            placeholder="What everyone in this group knows..."
            onChange={(value) => {
              setCommon(value);
              setError(null);
            }}
          />
        </div>
        <div className="drawer-foot">
          {error && <div className="form-note">{error}</div>}
          <div className="drawer-actions">
            <button className="drawer-save" onClick={() => void save()} disabled={busy || !ready}>
              {saved ? 'Saved' : 'Save'}
            </button>
          </div>
        </div>
      </aside>
    </>
  );
}

import { useEffect, useRef, useState } from 'react';
import { limitState } from '../profile';
import { Markdown } from './MessageBubble';

/**
 * A long text a member carries: written as markdown, read as markdown.
 * The area grows with the text until half the screen, then it scrolls.
 */
export default function MarkdownField({
  label,
  hint,
  value,
  onChange,
  placeholder,
  max,
}: {
  label: string;
  hint: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  /** The limit of the field, as the configuration says it. */
  max: number;
}) {
  const [preview, setPreview] = useState(false);
  const area = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = 'auto';
    const ceiling = Math.round(window.innerHeight * 0.5);
    el.style.height = `${Math.min(el.scrollHeight, ceiling)}px`;
  }, [value, preview]);
  const limit = limitState(value, max);
  return (
    <div className="field-block">
      <div className="field-head">
        <span className="field-label">{label}</span>
        <div className="field-toggle" role="group" aria-label={`${label} view`}>
          <button
            type="button"
            className={preview ? '' : 'active'}
            onClick={() => setPreview(false)}
          >
            Edit
          </button>
          <button
            type="button"
            className={preview ? 'active' : ''}
            onClick={() => setPreview(true)}
          >
            Preview
          </button>
        </div>
      </div>
      {preview ? (
        <div className="md-preview">
          {value.trim() ? (
            <Markdown text={value} />
          ) : (
            <p className="md-empty">Nothing written yet.</p>
          )}
        </div>
      ) : (
        <textarea
          ref={area}
          className="md-area"
          value={value}
          spellCheck={false}
          placeholder={placeholder}
          maxLength={max}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
      <div className="field-foot">
        <span className={`field-count${limit.ok ? '' : ' over'}`}>
          {limit.count.toLocaleString()} / {max.toLocaleString()}
        </span>
      </div>
      <p className="field-hint">{hint}</p>
    </div>
  );
}

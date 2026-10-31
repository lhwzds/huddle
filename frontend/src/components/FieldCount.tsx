import { limitState } from '../profile';

/**
 * The characters a text uses and the limit it has to keep, both as the
 * configuration of the app says them.
 */
export default function FieldCount({ value, max }: { value: string; max: number }) {
  const limit = limitState(value, max);
  return (
    <span className={`field-count${limit.ok ? '' : ' over'}`}>
      {limit.count.toLocaleString()} / {max.toLocaleString()}
    </span>
  );
}

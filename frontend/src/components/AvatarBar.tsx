import { avatarInitial, avatarSize, type RosterAgent } from '../avatarBar';

export default function AvatarBar({
  agents,
  onOpen,
  onAdd,
}: {
  agents: RosterAgent[];
  onOpen: (key: string) => void;
  onAdd: () => void;
}) {
  // The bar keeps every member on one row: the avatars shrink as the
  // group grows, and the row scrolls when it no longer fits.
  const size = avatarSize(agents.length + 1);
  return (
    <div className="avatar-bar">
      <div className="avatar-bar-track">
        {agents.map((agent) => (
          <button
            key={agent.key}
            className={`avatar-slot${agent.typing ? ' typing' : ''}`}
            onClick={() => onOpen(agent.key)}
            title={agent.known ? `${agent.name} — ${agent.role}` : agent.key}
          >
            <span
              className="avatar avatar-bar-avatar"
              style={{
                backgroundColor: agent.color,
                width: size,
                height: size,
                fontSize: Math.round(size * 0.36),
              }}
              aria-hidden="true"
            >
              {avatarInitial(agent.name)}
            </span>
            <span className="avatar-slot-name">{agent.name}</span>
          </button>
        ))}
        <button
          className="avatar-slot add"
          onClick={onAdd}
          title="Add a member"
          aria-label="Add a member"
        >
          <span
            className="avatar avatar-bar-avatar add-avatar"
            style={{ width: size, height: size, fontSize: Math.round(size * 0.42) }}
            aria-hidden="true"
          >
            ＋
          </span>
          <span className="avatar-slot-name">Add</span>
        </button>
      </div>
    </div>
  );
}

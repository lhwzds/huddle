export default function TurnDivider({ turn, time }: { turn: number; time: string }) {
  const date = new Date(time);
  const label = isNaN(date.getTime())
    ? ''
    : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return (
    <div className="turn-divider">
      <span>
        TURN {turn}{' '}
        {label && (
          <>
            {' '}
            <b>·</b> {label}
          </>
        )}
      </span>
    </div>
  );
}

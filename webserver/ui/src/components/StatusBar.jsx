export default function StatusBar({ latest, stale, connected }) {
  const level = (latest?.level || 'INFO').toUpperCase();
  const modifier = level === 'ERROR'
    ? 'is-error'
    : (level === 'WARNING' || level === 'WARN' ? 'is-warn' : '');
  const className = [
    'status-bar',
    modifier,
    stale ? 'is-stale' : '',
  ].filter(Boolean).join(' ');

  let text;
  if (latest?.message) {
    text = latest.message;
  } else if (stale) {
    text = 'Lost connection to kato. Retrying…';
  } else if (connected) {
    text = 'Connected to kato — waiting for the next scan tick.';
  } else {
    text = 'Connecting to kato…';
  }

  return (
    <div id="status-bar" className={className} title="Live kato activity">
      <span id="status-bar-pulse" aria-hidden="true" />
      <span id="status-bar-text">{text}</span>
    </div>
  );
}

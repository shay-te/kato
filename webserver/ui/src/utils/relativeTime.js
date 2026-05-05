// Tiny humanizer for "N minutes ago" style strings.
// Reused by the Adopt-Claude-Session modal, kept in utils so the
// tests don't have to instantiate the modal component to exercise
// the formatting rules.
export function formatRelativeTime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) { return '—'; }
  if (seconds < 60) { return 'just now'; }
  if (seconds < 3600) { return `${Math.floor(seconds / 60)}m ago`; }
  if (seconds < 86400) { return `${Math.floor(seconds / 3600)}h ago`; }
  return `${Math.floor(seconds / 86400)}d ago`;
}

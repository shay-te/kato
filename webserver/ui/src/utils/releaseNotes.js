// What the Release notes dialog shows, derived from GET /api/release-notes.
//
// Every entry in the file carries an invisible ``<!-- kato:task ID -->``
// marker (it is how kato never adds a task twice). They are stripped here,
// once, instead of trusting the markdown renderer to hide HTML comments.

const MARKER = /[ \t]*<!--\s*kato:task\s+\S+?\s*-->/g;

export function releaseNotesForDisplay(markdown) {
  return String(markdown || '').replace(MARKER, '');
}

// One sentence on the Google Doc copy, or '' when it is not wanted. ``tone``
// is 'error' only for a failed copy — that is what the operator must act on.
export function googleCopyLine(google, nowMs = Date.now()) {
  if (!google?.wanted) { return { text: '', tone: '' }; }
  if (google.error) { return { text: `Google Doc copy failed: ${google.error}`, tone: 'error' }; }
  if (!google.last_synced_at) { return { text: 'Not copied to the Google Doc yet.', tone: '' }; }
  return { text: `Copied to the Google Doc ${sinceText(google.last_synced_at * 1000, nowMs)}.`, tone: '' };
}

function sinceText(thenMs, nowMs) {
  const minutes = Math.max(0, Math.round((nowMs - thenMs) / 60000));
  if (minutes < 1) { return 'just now'; }
  if (minutes < 60) { return `${minutes}m ago`; }
  const hours = Math.round(minutes / 60);
  if (hours < 24) { return `${hours}h ago`; }
  return `on ${new Date(thenMs).toISOString().slice(0, 10)}`;
}

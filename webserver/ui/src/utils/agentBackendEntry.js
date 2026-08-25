import { backendLabel } from '../components/AgentBackendChip.jsx';

// Normalise one ``/api/agent-backends`` entry.
//
// The route used to return bare ids (``['claude', 'codex']``) and now returns
// an object per backend carrying its readiness. Both shapes are accepted on
// purpose: the UI bundle and the Python process are deployed as one repo but
// RESTART SEPARATELY, so a browser reload picks up the new bundle while the
// old server is still answering. Reading only the new shape made the whole
// agent-tab strip render blank until kato itself was restarted — a UI that
// disappears is a far worse failure than one that briefly lacks readiness.
//
// A bare id is assumed READY: the old server had no probe, and every backend
// it listed was one it had wired.
export function normalizeBackendEntry(raw) {
  if (typeof raw === 'string') {
    const id = raw.trim();
    if (!id) { return null; }
    return {
      id,
      label: backendLabel(id) || id,
      ready: true,
      wired: true,
      chat_available: true,
      error: '',
    };
  }
  const id = String(raw?.id || '').trim();
  if (!id) { return null; }
  return {
    id,
    label: String(raw.label || backendLabel(id) || id),
    // Absent flags mean "no probe ran", which must read as ready — never
    // as broken, or a partial payload hides a working chat behind a setup
    // panel.
    ready: raw.ready !== false,
    wired: raw.wired !== false,
    chat_available: raw.chat_available !== false,
    error: String(raw.error || ''),
  };
}

export function normalizeBackendEntries(list) {
  return (Array.isArray(list) ? list : [])
    .map(normalizeBackendEntry)
    .filter(Boolean);
}

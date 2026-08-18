// Per-task history of prompts the operator has sent, for shell-style
// recall with the up arrow.
//
// Kept in localStorage rather than in React state so it survives a
// reload and a kato restart — the whole value of "give me back what I
// just typed" is that it works when you did NOT plan ahead. Scoped per
// task because prompts are task-specific; recalling another task's
// prompt into this composer would be worse than recalling nothing.

const KEY_PREFIX = 'kato.promptHistory.v1.';
// Enough to walk back through a work session, small enough that the
// serialized blob stays trivial next to the draft/image stores.
const MAX_ENTRIES = 50;

function storageKey(taskId) {
  return `${KEY_PREFIX}${taskId || 'unknown'}`;
}

export function readPromptHistory(taskId) {
  if (!taskId) { return []; }
  try {
    const raw = window.localStorage.getItem(storageKey(taskId));
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((v) => typeof v === 'string') : [];
  } catch {
    return [];
  }
}

// Newest first. Consecutive duplicates collapse: re-sending the same
// prompt twice should not mean pressing up twice to get past it.
export function rememberPrompt(taskId, text) {
  const trimmed = String(text || '').trim();
  if (!taskId || !trimmed) { return readPromptHistory(taskId); }
  const existing = readPromptHistory(taskId);
  if (existing[0] === trimmed) { return existing; }
  const next = [trimmed, ...existing.filter((v) => v !== trimmed)].slice(0, MAX_ENTRIES);
  try {
    window.localStorage.setItem(storageKey(taskId), JSON.stringify(next));
  } catch {
    // Quota or private-mode: recall is a convenience, never a hard failure.
  }
  return next;
}

export function forgetPromptHistory(taskId) {
  if (!taskId) { return; }
  try {
    window.localStorage.removeItem(storageKey(taskId));
  } catch {
    // ignore
  }
}

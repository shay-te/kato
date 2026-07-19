// Per-task, per-action timestamp of the last time the operator ran a git
// action (push / pull / merge) FROM THIS BROWSER, shown in each action
// button's tooltip. The buttons are enabled whenever kato + the repos are
// ready (one op at a time) — they do NOT try to pre-compute "is there
// anything to do?" — so this "you last did it at <time>" is the operator's
// cue for whether a repeat click is redundant, without any remote-state
// guessing. localStorage-backed so it survives reloads; device-local by
// nature (it records YOUR clicks in THIS browser).

const STORAGE_KEY = 'kato.lastGitAction.v1';

function _readAll() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (_) {
    return {};
  }
}

// Epoch ms of the last `action` on `taskId`, or 0 when never run here.
export function getLastGitActionAt(taskId, action) {
  if (!taskId || !action) { return 0; }
  const perTask = _readAll()[taskId];
  const at = perTask && perTask[action];
  return Number.isFinite(at) ? at : 0;
}

// Record that `action` ran on `taskId` at `now` (defaults to real "now").
export function recordGitActionNow(taskId, action, now = Date.now()) {
  if (!taskId || !action) { return; }
  const all = _readAll();
  all[taskId] = { ...(all[taskId] || {}), [action]: now };
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(all));
  } catch (_) {
    // localStorage disabled / over quota — non-fatal, we just won't show the
    // last-run time.
  }
}

// "Jul 18, 2:32 PM" in the operator's local time; '' for a never-run action.
export function formatGitActionTime(epochMs) {
  if (!epochMs || !Number.isFinite(epochMs)) { return ''; }
  const date = new Date(epochMs);
  if (Number.isNaN(date.getTime())) { return ''; }
  return date.toLocaleString([], {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

// The " · Last <pastVerb>: <time>" suffix appended to a ready button's
// tooltip (or a "not … from here yet" note). ``pastVerb`` is the past tense
// ("pushed" / "pulled" / "merged"). Kept here so the component stays
// logic-free.
export function lastActionSuffix(taskId, action, pastVerb) {
  const when = formatGitActionTime(getLastGitActionAt(taskId, action));
  return when
    ? ` · Last ${pastVerb}: ${when}`
    : ` · Not ${pastVerb} from here yet`;
}

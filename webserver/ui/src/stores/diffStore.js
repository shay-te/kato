// Single source of truth for a task's changeset (the parsed /api/diff
// payload), keyed by task id.
//
// Before this, the SAME diff payload was fetched + cached independently
// by two surfaces: FilesTab (for the file-tree diff badges, via
// buildFilesDiffMeta) AND DiffPane (for the rendered diff). Each kept its
// own useState + JSON.stringify signature guard, both re-fetched on every
// workspaceVersion bump, and FilesTab ALSO polled every 5s — so the two
// copies could sit up to 5s out of sync (the tree showing a just-changed
// file the diff pane hadn't picked up yet) and every bump cost ~2× the
// diff requests.
//
// Now both surfaces subscribe to THIS store: one fetch, one parse, one
// poll. The store parses the payload ONCE (parseRepoDiffs) and hands out a
// referentially-stable ``repoDiffs`` array — its identity only changes
// when the payload bytes change, so a memoized consumer bails on an idle
// poll. The per-task pub/sub + poll lifecycle is the shared
// ``createTaskCacheStore`` skeleton (same as commentStore). Diff data is
// READ-only, so there are no mutations here — just the fetch + poke.

import { fetchDiff } from '../api.js';
import { parseRepoDiffs } from '../diffModel.js';
import { createTaskCacheStore } from './taskCacheStore.js';

// Same cadence as the file-tree / changes auto-poll it replaces.
const POLL_INTERVAL_MS = 5000;
const EMPTY_REPO_DIFFS = [];
export const EMPTY_DIFF_SNAPSHOT = {
  repoDiffs: EMPTY_REPO_DIFFS, loading: false, error: '',
};

// Single-flight fetch + parse of the task's whole changeset. The signature
// guard keeps the parsed ``repoDiffs`` array identity stable across idle
// polls, so subscribers' memos bail instead of re-locating/re-rendering.
function _load(entry) {
  if (entry.inFlight) { return entry.inFlight; }
  const taskId = entry.taskId;
  entry.inFlight = Promise.resolve()
    .then(() => fetchDiff(taskId))
    .then((payload) => {
      const sig = JSON.stringify(payload);
      const changed = sig !== entry.sig;
      if (changed) {
        entry.sig = sig;
        entry.repoDiffs = parseRepoDiffs(payload);
      }
      const wasLoading = entry.loading;
      const hadError = !!entry.error;
      entry.loading = false;
      entry.error = '';
      if (changed || wasLoading || hadError) {
        _base.emit(entry, { repoDiffs: entry.repoDiffs, loading: false, error: '' });
      }
    })
    .catch((err) => {
      // Keep the last-known changeset on error so a transient failure
      // doesn't blank the diff / badges; just surface the message.
      const nextError = String(err && err.message ? err.message : err) || 'failed to load diff';
      const changed = entry.loading || entry.error !== nextError;
      entry.loading = false;
      entry.error = nextError;
      if (changed) {
        _base.emit(entry, { repoDiffs: entry.repoDiffs, loading: false, error: nextError });
      }
    })
    .finally(() => { entry.inFlight = null; });
  return entry.inFlight;
}

const _base = createTaskCacheStore({
  intervalMs: POLL_INTERVAL_MS,
  emptySnapshot: EMPTY_DIFF_SNAPSHOT,
  createEntryState: () => ({
    repoDiffs: EMPTY_REPO_DIFFS,
    loading: true,
    error: '',
    sig: '',
    inFlight: null,
    snapshot: { repoDiffs: EMPTY_REPO_DIFFS, loading: true, error: '' },
  }),
  load: _load,
});

export const diffStore = {
  subscribe: _base.subscribe,
  getSnapshot: _base.getSnapshot,
  poke: _base.poke,
};

// Diff child store — the task's parsed changeset (read-only). One fetch, one
// parse; hands out a referentially-stable ``repoDiffs`` shared by the centre
// diff pane and the file-tree badges. PRIVATE: only the parent (../index.js)
// wires and reads it. Namespace api import so a partial api.js mock can't
// break module load (see index.js).
//
// Every fetch after the first carries the ETag of the changeset on screen, so
// the usual answer to a poll is an empty 304: nothing crosses the wire and
// nothing is re-parsed. This is the heaviest payload the app polls — one
// 27-repository task measured 4.3 MB, rebuilt every five seconds and almost
// always identical.

import * as api from '../../../api.js';
import { parseRepoDiffs } from '../../../diffModel.js';
import { createDataStore, signed } from '../createDataStore.js';

async function fetchDiff(taskId, { signature }) {
  const result = await api.fetchDiff(taskId, { signature });
  if (result.unchanged) {
    // The server confirmed what is on screen. The same signature with no
    // payload is how the store is told to keep it and parse nothing.
    return signed(null, signature);
  }
  // No ETag (something stripped it) → unsigned, and the store falls back to
  // comparing the payload's own bytes.
  return result.etag ? signed(result.payload, result.etag) : result.payload;
}

export const diffChild = createDataStore({
  fetch: fetchDiff,
  parse: parseRepoDiffs,
  empty: [],
});

// Diff child store — the task's parsed changeset (read-only). One fetch, one
// parse; hands out a referentially-stable ``repoDiffs`` shared by the centre
// diff pane and the file-tree badges. PRIVATE: only the parent (../index.js)
// wires and reads it. Namespace api import so a partial api.js mock can't
// break module load (see index.js).

import * as api from '../../../api.js';
import { parseRepoDiffs } from '../../../diffModel.js';
import { createDataStore } from '../createDataStore.js';

export const diffChild = createDataStore({
  fetch: (taskId) => api.fetchDiff(taskId),
  parse: parseRepoDiffs,
  empty: [],
});

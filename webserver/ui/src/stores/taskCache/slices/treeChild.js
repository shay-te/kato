// File-tree child store — the task's workspace file tree (all repos), fetched
// from /files and normalized once. Hands out a referentially-stable ``trees``
// so the tree rows + memos bail on an unchanged poll (the old FilesTab
// ``fetchSigRef`` dedupe, now shared). PRIVATE: only the parent (../index.js)
// wires and reads it.

import * as api from '../../../api.js';
import { normalizeTrees } from '../../../FilesTabHelpers.js';
import { createDataStore } from '../createDataStore.js';
import { createPersistedPayloads } from '../persistedPayloads.js';
import { durableConfig } from '../durableTypes.js';

export const treeChild = createDataStore({
  fetch: (taskId) => api.fetchFileTree(taskId),
  parse: normalizeTrees,
  empty: [],
  // Durability is DECLARED in ../durableTypes.js, not decided here — that
  // registry is what the contract test reads, and what forces the next slice
  // to answer the question instead of quietly defaulting to "no".
  persist: createPersistedPayloads(durableConfig('tree')),
});

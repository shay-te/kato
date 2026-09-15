// One-time cleanup of the file-tree copies the browser used to keep.
//
// The Files tree was once stored in IndexedDB (up to 16 MB a task, for five
// tasks) so a reload could paint it. The server caches it now
// (webserver/kato_webserver/file_tree_cache.py), which leaves those copies as
// dead weight in the operator's browser storage. This deletes them. Running it
// again finds nothing and does nothing; the file can go once no browser can
// still be holding the old keys.

import { idbDelete, idbGet } from './idbStore.js';

const LEGACY_PREFIX = 'kato.taskCache.tree.';
const LEGACY_INDEX_KEY = `${LEGACY_PREFIX}index`;

export async function removeLegacyTreeCache() {
  try {
    const ids = await idbGet(LEGACY_INDEX_KEY);
    if (!Array.isArray(ids)) { return; }
    const taskIds = ids.filter((id) => typeof id === 'string' && id);
    await Promise.all(taskIds.map((id) => idbDelete(`${LEGACY_PREFIX}${id}`)));
    await idbDelete(LEGACY_INDEX_KEY);
  } catch (_err) {
    // Best-effort: storage that cannot be read holds nothing to clean.
  }
}

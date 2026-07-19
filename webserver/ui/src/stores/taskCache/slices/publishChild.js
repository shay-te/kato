// Publish child store — a task's LOCAL git-button state (workspace present +
// local push-readiness). Fast, no provider call — drives the push/pull/merge
// buttons' enabled state. PRIVATE: only the parent (../index.js) wires it.

import * as api from '../../../api.js';
import { createDataStore } from '../createDataStore.js';

export const publishChild = createDataStore({
  fetch: (taskId) => api.fetchTaskPublishState(taskId),
  parse: (body) => ({
    hasWorkspace: !!body?.has_workspace,
    hasChangesToPush: !!body?.has_changes_to_push,
  }),
  empty: { hasWorkspace: false, hasChangesToPush: false },
});

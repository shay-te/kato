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
    branch: String(body?.branch || ''),
    // A plan sub-task's base: the branch of the task it was split from, which
    // its git actions use where any other task uses master ('' = not one).
    mergeBranch: String(body?.merge_branch || ''),
  }),
  empty: { hasWorkspace: false, hasChangesToPush: false, branch: '', mergeBranch: '' },
});

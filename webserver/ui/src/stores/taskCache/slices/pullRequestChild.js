// Pull-request child store — a task's PR-existence + open-PR link. A live
// provider call, so best-effort and kept OFF the git-button path: its failures
// never disable the buttons (the parent never surfaces this child's error as
// the publish ready/error). PRIVATE: only the parent (../index.js) wires it.

import * as api from '../../../api.js';
import { createDataStore } from '../createDataStore.js';

export const pullRequestChild = createDataStore({
  fetch: (taskId) => api.fetchTaskPullRequestState(taskId),
  parse: (body) => ({
    hasPullRequest: !!body?.has_pull_request,
    pullRequestUrls: Array.isArray(body?.pull_request_urls)
      ? body.pull_request_urls.filter(Boolean) : [],
    // A PERMANENT provider refusal (401/403) behind this answer. Carried as
    // DATA, never as the child's ``error`` — that would put it back on the
    // git-button path this store exists to stay off. Transient failures
    // (429, network) stay silent; those heal on their own.
    lookupError: String(body?.lookup_error || ''),
  }),
  empty: { hasPullRequest: false, pullRequestUrls: [], lookupError: '' },
});

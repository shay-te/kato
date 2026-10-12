// Pull-request child store — a task's PR-existence, its PR links and each
// repository's PR. A live
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
    // Every repository on the task with its open PR (null = none), and
    // ``lookupFailed`` when the provider could not be asked — the header's
    // pull-request list, so no PR is missed. null when the server did not
    // list them at all (a kato older than this page): "no repositories"
    // would be a guess.
    repositories: Array.isArray(body?.repositories) ? parseRepositories(body.repositories) : null,
  }),
  empty: { hasPullRequest: false, pullRequestUrls: [], lookupError: '', repositories: [] },
});

function parseRepositories(items) {
  return (Array.isArray(items) ? items : [])
    .map((item) => ({
      repositoryId: String(item?.repository_id || ''),
      pullRequest: item?.pull_request ? {
        id: String(item.pull_request.id || ''),
        title: String(item.pull_request.title || ''),
        url: String(item.pull_request.url || ''),
      } : null,
      lookupFailed: !!item?.lookup_failed,
    }))
    .filter((repository) => repository.repositoryId);
}

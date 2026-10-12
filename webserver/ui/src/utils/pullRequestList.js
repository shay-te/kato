// The header's pull-request list, in words: how a task's repositories stand on
// their pull requests. Pure — PullRequestList.jsx renders these.
//
// ``repositories`` is the pull-request store's list: ``{ repositoryId,
// pullRequest: { id, title, url } | null, lookupFailed }`` per repository.

// How many repositories have an open pull request, of how many, and how many
// the provider could not be asked about.
export function pullRequestCounts(repositories) {
  const list = repositories || [];
  return {
    open: list.filter((repository) => repository.pullRequest).length,
    total: list.length,
    unchecked: list.filter((repository) => repository.lookupFailed).length,
  };
}

// The badge on the header button: "2/3" once any pull request is open, ''
// before — no PR yet is the ordinary state of a task in progress.
export function pullRequestBadge(repositories) {
  const { open, total } = pullRequestCounts(repositories);
  return open ? `${open}/${total}` : '';
}

// The badge's colour family: 'good' when every repository has its pull
// request, 'warn' while some still have none.
export function pullRequestTone(repositories) {
  const { open, total } = pullRequestCounts(repositories);
  return open && open === total ? 'good' : 'warn';
}

// ``{ disabled, tooltip }`` for the header button. Only an answer from the
// server is a list: still loading, failed, or not listed by an older kato is
// said as such — never as "no repositories", which is what got reported.
export function pullRequestButtonState({ repositories, status = 'ready', fetchError = '' } = {}) {
  if (status === 'error') {
    return {
      disabled: false,
      tooltip: `Could not check the pull requests${fetchError ? ` (${fetchError})` : ''}. Click to try again.`,
    };
  }
  if (status === 'idle' || status === 'loading') {
    return { disabled: true, tooltip: 'Checking the pull requests…' };
  }
  if (!Array.isArray(repositories)) {
    return {
      disabled: true,
      tooltip: 'This kato server is older than this page and does not list pull requests '
        + 'per repository. Restart kato.',
    };
  }
  return { disabled: repositories.length === 0, tooltip: pullRequestListTooltip(repositories) };
}

export function pullRequestListTooltip(repositories) {
  const { open, total, unchecked } = pullRequestCounts(repositories);
  if (!total) { return 'Pull requests — this task has no repositories to check.'; }
  const repos = total === 1 ? 'repository' : 'repositories';
  const parts = [`Pull requests open in ${open} of ${total} ${repos}`];
  if (unchecked) { parts.push(`${unchecked} could not be checked`); }
  return `${parts.join('; ')}. Click for the list — open or copy each one.`;
}

// "#42 Add login" · "#42" · "Add login" · "Pull request" — what a row calls
// its pull request.
export function pullRequestLabel(pullRequest) {
  const number = pullRequest?.id ? `#${pullRequest.id}` : '';
  return [number, pullRequest?.title].filter(Boolean).join(' ') || 'Pull request';
}

// The links "Open all" opens, in the list's order.
export function pullRequestUrls(repositories) {
  return (repositories || []).map((repository) => repository.pullRequest?.url).filter(Boolean);
}

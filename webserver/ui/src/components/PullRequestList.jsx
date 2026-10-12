import { useRef, useState } from 'react';
import Icon from './Icon.jsx';
import { cx } from '../utils/cx.js';
import { useCopyAction } from '../hooks/useCopyAction.js';
import { useDismissOnOutsidePointerOrEscape } from '../hooks/useDismissOnOutsidePointerOrEscape.js';
import {
  pullRequestBadge,
  pullRequestButtonState,
  pullRequestCounts,
  pullRequestLabel,
  pullRequestTone,
  pullRequestUrls,
} from '../utils/pullRequestList.js';

// The task's pull requests, from the header — one per repository, so none is
// missed (to merge, or to hand to reviewers), whichever host each lives on.
// The badge counts the open ones ("2/3"); a click lists every repository with
// its pull request to open in a new tab or copy, or says it has none yet — or
// that kato could not ask, which is not the same thing.
//
// ``onOpen`` runs as the list opens, to look the pull requests up again: they
// are fetched on demand, never polled. ``status`` / ``fetchError`` are that
// lookup's: loading or failed is said as such, never as an empty list — and a
// failed one can be tried again from the list (``onOpen``'s promise says when
// that try is over).
export default function PullRequestList({
  repositories: listed = [], status = 'ready', fetchError = '', lookupError = '', onOpen = () => {},
}) {
  const [open, setOpen] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const rootRef = useRef(null);
  useDismissOnOutsidePointerOrEscape(open, () => setOpen(false), rootRef);

  const button = pullRequestButtonState({ repositories: listed, status, fetchError });
  const repositories = Array.isArray(listed) ? listed : [];
  const badge = pullRequestBadge(repositories);
  const { open: openCount, total } = pullRequestCounts(repositories);
  const urls = pullRequestUrls(repositories);
  function toggle() {
    if (!open) { onOpen(); }
    setOpen((was) => !was);
  }
  function retry() {
    setRetrying(true);
    Promise.resolve(onOpen()).finally(() => setRetrying(false));
  }
  // A click is a direct user gesture, so the browser allows the batch;
  // ``noopener,noreferrer`` keeps the pages from reaching back into kato.
  function openAll() {
    urls.forEach((url) => { window.open(url, '_blank', 'noopener,noreferrer'); });
  }
  const badgeEl = badge ? (
    <span className={cx('pull-request-list-badge', `is-${pullRequestTone(repositories)}`)} aria-hidden="true">
      {badge}
    </span>
  ) : null;
  let note = lookupError ? <p className="composer-mode-popover-note is-warn">{lookupError}</p> : null;
  if (status === 'error') {
    note = (
      <div className="composer-mode-popover-note is-warn">
        {`Could not check the pull requests${fetchError ? ` (${fetchError})` : ''}.`}
        <button type="button" className="composer-mode-release" onClick={retry} disabled={retrying}>
          {retrying ? 'Checking again…' : 'Try again'}
        </button>
      </div>
    );
  }
  const rows = repositories.map((repository) => (
    <PullRequestRow key={repository.repositoryId} repository={repository} />
  ));
  const openAllButton = urls.length > 1 ? (
    <button type="button" className="pull-request-list-open-all" onClick={openAll}>
      {`Open all ${urls.length} in new tabs`}
    </button>
  ) : null;
  const listTitle = `Pull requests · ${openCount} of ${total} open`;
  const popover = open ? (
    <div className="pull-request-list" role="dialog" aria-label="Pull requests">
      <div className="composer-mode-popover-title">{listTitle}</div>
      {note}
      <ul className="pull-request-list-rows">{rows}</ul>
      {openAllButton}
    </div>
  ) : null;

  return (
    <div className="pull-request-list-wrap" ref={rootRef}>
      <button
        id="session-open-pull-request"
        type="button"
        className="session-action pull-request-list-toggle"
        data-tooltip={button.tooltip}
        aria-label={`Pull requests: ${openCount} of ${total} open`}
        aria-haspopup="dialog"
        aria-expanded={open}
        disabled={button.disabled}
        onClick={toggle}
      >
        <Icon name="external-link" />
        {badgeEl}
      </button>
      {popover}
    </div>
  );
}

function PullRequestRow({ repository }) {
  const { copied, copy } = useCopyAction();
  const { repositoryId, pullRequest, lookupFailed } = repository;
  const url = pullRequest?.url || '';
  const status = rowStatus(pullRequest, lookupFailed);
  const copyLabel = copied ? 'Link copied' : `Copy the ${repositoryId} pull request link`;
  const actions = url ? (
    <span className="pull-request-list-actions">
      <a
        className="pull-request-list-action"
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        aria-label={`Open the ${repositoryId} pull request in a new tab`}
        data-tooltip="Open in a new tab"
      >
        <Icon name="external-link" />
      </a>
      <button
        type="button"
        className="pull-request-list-action"
        aria-label={copyLabel}
        data-tooltip={copied ? 'Copied' : 'Copy the link'}
        onClick={(event) => copy(url, event)}
      >
        <Icon name={copied ? 'check' : 'copy'} />
      </button>
    </span>
  ) : null;
  return (
    <li className={cx('pull-request-list-row', pullRequest && 'has-pull-request')}>
      <span className="pull-request-list-repo">{repositoryId}</span>
      {status}
      {actions}
    </li>
  );
}

// The pull request, or why there is none to show: none yet, or kato could not
// ask the git host — which must never read as "none".
function rowStatus(pullRequest, lookupFailed) {
  if (pullRequest) {
    const label = pullRequestLabel(pullRequest);
    return <span className="pull-request-list-title" title={label}>{label}</span>;
  }
  if (lookupFailed) {
    return <span className="pull-request-list-status is-warn">Could not check — the git host did not answer</span>;
  }
  return <span className="pull-request-list-status">No pull request yet</span>;
}

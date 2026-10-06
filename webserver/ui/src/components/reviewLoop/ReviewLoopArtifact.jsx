import { useEffect, useState } from 'react';
import { fetchReviewLoopArtifact } from '../../api.js';
import MarkdownContent from '../MarkdownContent.jsx';
import Icon from '../Icon.jsx';

// One round's saved text — the reviewer's full report, the message posted to
// the chat, or the diff the reviewer was shown — fetched only when opened:
// a diff can be hundreds of KB, and most rounds are never expanded.
export default function ReviewLoopArtifact({ taskId, loopId, round, kind, label }) {
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState({ status: 'idle', text: '' });
  // One fetch, the first time it is opened. The status is NOT set to
  // "loading" from inside the effect: with the status among the effect's
  // inputs, that change re-ran the effect, whose cleanup cancelled the fetch
  // in flight — every report sat on "Loading…" forever. Opened and still idle
  // already reads as loading (``artifactBody``).
  const needsFetch = open && loaded.status === 'idle';
  useEffect(() => {
    if (!needsFetch) { return undefined; }
    let cancelled = false;
    Promise.resolve(fetchReviewLoopArtifact(taskId, loopId, round, kind)).then((result) => {
      if (cancelled) { return; }
      setLoaded(result?.ok
        ? { status: 'ready', text: String(result.body?.text || '') }
        : { status: 'error', text: result?.error || 'could not load it' });
    });
    return () => { cancelled = true; };
  }, [needsFetch, taskId, loopId, round, kind]);
  const body = open ? artifactBody(kind, loaded) : null;
  const chevron = open ? 'chevron-down' : 'chevron-right';
  return (
    <div className="review-loop-artifact" data-kind={kind}>
      <button
        type="button"
        className="review-loop-artifact-toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <Icon name={chevron} />
        <span>{label}</span>
      </button>
      {body}
    </div>
  );
}

function artifactBody(kind, loaded) {
  if (loaded.status === 'loading' || loaded.status === 'idle') {
    return <p className="review-loop-artifact-note">Loading…</p>;
  }
  if (loaded.status === 'error') {
    return <p className="review-loop-artifact-note is-error">Couldn’t load it: {loaded.text}</p>;
  }
  if (kind === 'review') {
    return (
      <div className="review-loop-artifact-body is-markdown">
        <MarkdownContent>{loaded.text}</MarkdownContent>
      </div>
    );
  }
  return <pre className="review-loop-artifact-body">{loaded.text}</pre>;
}

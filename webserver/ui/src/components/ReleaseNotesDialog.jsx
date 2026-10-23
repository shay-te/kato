import { useState } from 'react';
import { fetchReleaseNotes, syncReleaseNotes } from '../api.js';
import { useCopyAction } from '../hooks/useCopyAction.js';
import { useDelayedFlag } from '../hooks/useDelayedFlag.js';
import { useEscapeKey } from '../hooks/useEscapeKey.js';
import { usePickerData } from '../hooks/usePickerData.js';
import { googleCopyLine, releaseNotesForDisplay } from '../utils/releaseNotes.js';
import MarkdownContent from './MarkdownContent.jsx';
import ModalShell from './ModalShell.jsx';

// The release notes, read-only: a dated line per task that reached Done.
//
// The file is the master and the operator edits it in their own editor (rename
// "Unreleased" to a version when releasing); this view only shows it, says
// where it is, and — when the Google Doc copy is chosen in Settings — how the
// copy stands, with "Sync now". Read when it opens and on Refresh; never polled.
export default function ReleaseNotesDialog({ onClose }) {
  const [reads, setReads] = useState(0);
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState('');
  const { data, loading, error } = usePickerData(fetchReleaseNotes, [reads], null);
  const { copied, copy } = useCopyAction();
  const showLoader = useDelayedFlag(loading && !data, 250);
  useEscapeKey(onClose);
  const google = googleCopyLine(data?.google);
  const reread = () => setReads((count) => count + 1);

  async function syncNow() {
    setSyncing(true);
    setSyncError('');
    const result = await syncReleaseNotes();
    setSyncing(false);
    if (!result.ok) {
      setSyncError(result.body?.error || result.error || 'the copy failed');
    }
    reread();
  }

  return (
    <ModalShell ariaLabel="Release notes" title="Release notes" extraClass="release-notes-dialog" onClose={onClose}>
      <div className="release-notes-body">
        <div className="release-notes-toolbar">
          <code className="release-notes-path" title={data?.path || ''}>{data?.path || ''}</code>
          <button type="button" className="release-notes-command" disabled={!data?.path}
            onClick={(event) => copy(data?.path, event)}>
            {copied ? 'Copied' : 'Copy path'}
          </button>
          <button type="button" className="release-notes-command" disabled={loading} onClick={reread}>
            {loading && data ? 'Reading…' : 'Refresh'}
          </button>
          {data?.google?.wanted ? (
            <button type="button" className="release-notes-command" disabled={syncing} onClick={syncNow}>
              {syncing ? 'Copying…' : 'Sync now'}
            </button>
          ) : null}
        </div>

        {google.text ? (
          <p className={`release-notes-google${google.tone === 'error' ? ' is-error' : ''}`}
            role={google.tone === 'error' ? 'alert' : undefined}>
            {google.text}
          </p>
        ) : null}
        {syncError ? <p className="release-notes-google is-error" role="alert">{syncError}</p> : null}
        {error ? <p className="release-notes-google is-error" role="alert">Could not read the release notes: {error}</p> : null}
        {showLoader ? <p className="release-notes-note" role="status">Reading the release notes…</p> : null}
        {data && !data.enabled ? (
          <p className="release-notes-note">Release notes are switched off — Settings → Release notes.</p>
        ) : null}
        {data && data.enabled && !data.markdown ? (
          <p className="release-notes-note">
            Nothing yet. When a task reaches Done, kato adds a dated line for it here.
          </p>
        ) : null}
        {data?.markdown ? (
          <div className="release-notes-content">
            <MarkdownContent>{releaseNotesForDisplay(data.markdown)}</MarkdownContent>
          </div>
        ) : null}

        <p className="release-notes-foot">
          A task is added when it reaches Done: "Finish &amp; forget" with done ticked, the plan board's
          "Count as done", or the ticket closed in YouTrack / Jira. The file is yours to edit — rename
          "Unreleased" to a version when you release. Where it lives, and the Google Doc copy, are in
          Settings → Release notes.
        </p>
      </div>
    </ModalShell>
  );
}

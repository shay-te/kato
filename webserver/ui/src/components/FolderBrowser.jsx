import { useCallback, useEffect, useState } from 'react';
import { fetchDirectoryListing } from '../api.js';
import Icon from './Icon.jsx';

// Inline folder picker behind the "Browse…" button (wizard step 3 and
// Settings → Repositories). The planning webserver runs on the operator's
// machine, so it can list REAL directories — something a browser's native
// file input can't do (it never reveals absolute paths). Inline (not a
// portal modal) so it works inside the setup gate without z-index games.
//
// The folder it opens on may not be listable — one kato offers to CREATE
// (quick start's ~/code) does not exist yet. It then opens on the home folder
// and says so: it used to stop at "could not list that folder" with nothing
// to click (Up and Home both needed a listing first).
export default function FolderBrowser({ initialPath, onPick, onClose }) {
  const [listing, setListing] = useState(null);
  const [error, setError] = useState('');
  const [note, setNote] = useState('');

  // Whether it listed ``path``.
  const load = useCallback(async (path) => {
    setNote('');
    try {
      const body = await fetchDirectoryListing(path);
      if (body && body.path) {
        setListing(body);
        setError('');
        return true;
      }
      setError((body && body.error) || `could not open ${path}`);
    } catch (_) {
      setError(`could not open ${path}`);
    }
    return false;
  }, []);

  useEffect(() => {
    const start = initialPath || '~';
    load(start).then(async (listed) => {
      if (listed || start === '~') { return; }
      if (await load('~')) { setNote(`${start} could not be opened — showing your home folder.`); }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="folder-browser">
      <div className="folder-browser-head">
        <button
          type="button"
          className="folder-browser-nav"
          onClick={() => listing?.parent && load(listing.parent)}
          disabled={!listing?.parent}
          aria-label="Up one folder"
        >
          ↑ Up
        </button>
        <button
          type="button"
          className="folder-browser-nav"
          onClick={() => load(listing?.home || '~')}
          aria-label="Go to home folder"
        >
          ~ Home
        </button>
        {/* The box is right-to-left so a long path keeps its END in view; the
            path itself is isolated left-to-right, or its leading "/" lands at
            the end ("private/tmp/home/"). */}
        <code className="folder-browser-path"><bdi dir="ltr">{listing?.path || '…'}</bdi></code>
      </div>
      {note && <p className="folder-browser-note">{note}</p>}
      {error && <p className="folder-browser-error" role="alert">{error}</p>}
      <ul className="folder-browser-list">
        {(listing?.dirs || []).map((dir) => (
          <li key={dir.path}>
            <button
              type="button"
              className="folder-browser-entry"
              onClick={() => load(dir.path)}
            >
              <Icon name="folder" className="folder-browser-entry-icon" />
              <span>{dir.name}</span>
            </button>
          </li>
        ))}
        {listing && listing.dirs.length === 0 && (
          <li className="folder-browser-empty">(no subfolders)</li>
        )}
      </ul>
      <div className="folder-browser-actions">
        <button type="button" className="setup-wizard-btn" onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className="setup-wizard-btn setup-wizard-btn--primary"
          disabled={!listing?.path}
          onClick={() => listing?.path && onPick(listing.path)}
        >
          Use this folder
        </button>
      </div>
    </div>
  );
}

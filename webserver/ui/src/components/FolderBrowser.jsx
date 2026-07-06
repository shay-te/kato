import { useCallback, useEffect, useState } from 'react';
import { fetchDirectoryListing } from '../api.js';

// Inline folder picker behind the "Browse…" button (wizard step 3 and
// Settings → Repositories). The planning webserver runs on the operator's
// machine, so it can list REAL directories — something a browser's native
// file input can't do (it never reveals absolute paths). Inline (not a
// portal modal) so it works inside the setup gate without z-index games.
export default function FolderBrowser({ initialPath, onPick, onClose }) {
  const [listing, setListing] = useState(null);
  const [error, setError] = useState('');

  const load = useCallback(async (path) => {
    try {
      const body = await fetchDirectoryListing(path);
      if (body && body.path) {
        setListing(body);
        setError('');
      } else {
        setError((body && body.error) || 'could not list that folder');
      }
    } catch (_) {
      setError('could not list that folder');
    }
  }, []);

  useEffect(() => {
    load(initialPath || '~');
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
          onClick={() => listing?.home && load(listing.home)}
          aria-label="Go to home folder"
        >
          ~ Home
        </button>
        <code className="folder-browser-path">{listing?.path || '…'}</code>
      </div>
      {error && <p className="folder-browser-error" role="alert">{error}</p>}
      <ul className="folder-browser-list">
        {(listing?.dirs || []).map((dir) => (
          <li key={dir.path}>
            <button
              type="button"
              className="folder-browser-entry"
              onClick={() => load(dir.path)}
            >
              📁 {dir.name}
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

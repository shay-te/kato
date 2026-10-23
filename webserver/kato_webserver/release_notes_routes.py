"""``/api/release-notes`` — read the release notes; copy them to the Google Doc now.

``GET`` answers ``{enabled, path, exists, markdown, google}``: whether notes
are kept, the file (as configured in Settings — read fresh, like everything
release-notes), its content, and the state of the Google Doc copy
(``{wanted, document_id, last_synced_at, error}``).

``POST /api/release-notes/sync`` is the view's "Sync now": it copies the file
into the Google Doc at once and answers the new copy state. Refused (409)
when the Google Doc destination is not chosen in Settings.
"""

from __future__ import annotations

from flask import Flask, jsonify

from kato_core_lib.data_layers.service.release_notes_google_sync import ReleaseNotesGoogleSync
from kato_core_lib.helpers.release_notes_file_utils import read_release_notes
from kato_core_lib.helpers.release_notes_settings_utils import (
    release_notes_enabled,
    release_notes_path,
)


def register_release_notes_routes(app: Flask, *, google_sync: ReleaseNotesGoogleSync | None = None) -> None:
    sync = google_sync or ReleaseNotesGoogleSync()

    @app.get('/api/release-notes')
    def get_release_notes():
        path = release_notes_path()
        return jsonify({
            'enabled': release_notes_enabled(),
            'path': str(path),
            'exists': path.is_file(),
            'markdown': read_release_notes(path),
            'google': sync.status(),
        })

    @app.post('/api/release-notes/sync')
    def sync_release_notes():
        if not sync.wanted():
            return jsonify({
                'error': 'the Google Doc copy is off — choose "file_and_google_doc" in '
                         'Settings → Release notes',
            }), 409
        return jsonify(sync.sync_now())

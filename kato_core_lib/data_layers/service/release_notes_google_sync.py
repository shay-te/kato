"""Copy the release notes into a Google Doc, when that destination is chosen.

One job: after the release-notes file changes, replace the Doc's content with
it. The local file stays the master; the Doc is a read-only copy for whoever
reads it (anything typed into the Doc is overwritten by the next copy).

* Opt-in: only when Settings → Release notes destination is "file and
  Google Doc" — task titles and summaries then leave this machine.
* Bursts are one upload: a task adds a title line and, seconds later, the
  better line; a short wait collapses them.
* A failure never touches the local file. It is kept with the time of the
  last good copy (``release_notes_sync.json``) so the Release notes view can
  show it in red and offer "Sync now".

The transport is ``google_docs_core_lib`` (its own lib, a core-lib client);
this module only decides WHEN and WHAT to copy.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path

from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json

from kato_core_lib.helpers.kato_paths_utils import kato_home_path
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.release_notes_file_utils import read_release_notes
from kato_core_lib.helpers.release_notes_html_utils import release_notes_html
from kato_core_lib.helpers.release_notes_settings_utils import (
    DESTINATION_FILE_AND_GOOGLE_DOC,
    google_credentials_path,
    google_doc_id,
    release_notes_destination,
    release_notes_path,
)

# Long enough to catch the "better line" that follows the title line.
DEBOUNCE_SECONDS = 20.0


def _google_client(credentials_path: Path):
    from google_docs_core_lib.google_docs_core_lib.client.google_docs_client import GoogleDocsClient

    return GoogleDocsClient.from_key_file(credentials_path)


def _start_timer(seconds: float, work: Callable[[], None]) -> None:
    timer = threading.Timer(seconds, work)
    timer.daemon = True
    timer.start()


class ReleaseNotesGoogleSync(object):
    def __init__(
        self,
        *,
        client_factory: Callable[[Path], object] = _google_client,
        schedule: Callable[[float, Callable[[], None]], None] = _start_timer,
        status_path: Path | None = None,
        clock: Callable[[], float] = time.time,
        logger=None,
    ) -> None:
        self._client_factory = client_factory
        self._schedule = schedule
        self._status_path = status_path or kato_home_path(
            'release_notes_sync.json', env_key='KATO_RELEASE_NOTES_SYNC_STATUS_PATH',
        )
        self._clock = clock
        self._lock = threading.Lock()
        self._waiting = False
        self.logger = logger or configure_logger('ReleaseNotesGoogleSync')

    @staticmethod
    def wanted() -> bool:
        return release_notes_destination() == DESTINATION_FILE_AND_GOOGLE_DOC

    def request(self) -> None:
        """The file changed: copy it soon (several changes in a row, once)."""
        if not self.wanted():
            return
        with self._lock:
            if self._waiting:
                return
            self._waiting = True
        self._schedule(DEBOUNCE_SECONDS, self._run_waiting)

    def _run_waiting(self) -> None:
        with self._lock:
            self._waiting = False
        self.sync_now()

    def sync_now(self) -> dict:
        """Copy the file into the Doc now. Returns the new status; never raises."""
        if not self.wanted():
            return self.status()
        document = google_doc_id()
        credentials = google_credentials_path()
        if not document or credentials is None:
            return self._record(error='set the Google Doc link and the service-account key file in Settings')
        try:
            client = self._client_factory(credentials)
            client.replace_content(document, release_notes_html(read_release_notes(release_notes_path())))
        except Exception as exc:  # noqa: BLE001 - every failure is shown, none is fatal
            self.logger.warning('release notes: the Google Doc copy failed: %s', exc)
            return self._record(error=str(exc) or type(exc).__name__)
        self.logger.info('release notes copied to Google Doc %s', document)
        return self._record(error='', synced=True)

    def status(self) -> dict:
        """``{wanted, document_id, last_synced_at, error}`` for the Release notes view."""
        saved = self._load()
        return {
            'wanted': self.wanted(),
            'document_id': google_doc_id(),
            'last_synced_at': saved.get('last_synced_at', 0.0),
            'error': saved.get('error', ''),
        }

    def _record(self, *, error: str, synced: bool = False) -> dict:
        saved = self._load()
        saved['error'] = error
        if synced:
            saved['last_synced_at'] = self._clock()
        try:
            self._status_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # the write below says what went wrong
        atomic_write_json(
            self._status_path, saved, logger=self.logger, label='release notes Google Doc status',
        )
        return self.status()

    def _load(self) -> dict:
        try:
            data = json.loads(Path(self._status_path).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

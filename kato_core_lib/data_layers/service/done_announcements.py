"""Everyone told when a task reaches Done — the release notes, the Slack post.

``TaskStateService`` announces Done in ONE place and hands it one observer: this
one. Each is told on its own, so one that fails never keeps the next from
hearing it, and none of them can fail the done itself.
"""

from __future__ import annotations

from kato_core_lib.helpers.late_binding import provider_for
from kato_core_lib.helpers.logging_utils import configure_logger


class DoneAnnouncements(object):
    def __init__(self, observers, logger=None) -> None:
        self._observers = [provider_for(observer) for observer in observers]
        self.logger = logger or configure_logger('DoneAnnouncements')

    def task_done(self, task_id: str) -> None:
        for get_observer in self._observers:
            try:
                observer = get_observer()
                if observer is not None:
                    observer.task_done(task_id)
            except Exception:
                self.logger.exception('task %s is done, but telling one of its observers failed', task_id)

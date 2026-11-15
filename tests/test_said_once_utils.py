"""A standing condition is said once — again only when it changes, once when it ends.

2026-11-15, "why it's printing so much duplicates?": the review scan printed the
same forgotten-tasks line 38 times in 400 log lines and twenty identical 403s
per scan. ``SaidOnce`` is what every such line goes through now.
"""

from __future__ import annotations

import threading
import types
import unittest
from unittest.mock import MagicMock, patch

from kato_core_lib.data_layers.service.review_comment_service import ReviewCommentService
from kato_core_lib.helpers.said_once_utils import SaidOnce


class SaidOnceTests(unittest.TestCase):
    def test_said_once_again_when_it_changes_and_its_end_once(self) -> None:
        said = SaidOnce()
        self.assertTrue(said.say('k', 'a'))
        self.assertFalse(said.say('k', 'a'))
        self.assertTrue(said.say('k', 'b'))
        self.assertTrue(said.say('other', 'b'))  # keys are apart
        self.assertTrue(said.ended('k'))
        self.assertFalse(said.ended('k'))
        self.assertTrue(said.say('k', 'b'))  # after its end, news again

    def test_threads_say_it_once(self) -> None:
        said = SaidOnce()
        answers = []
        threads = [threading.Thread(target=lambda: answers.append(said.say('k', 'x'))) for _ in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(answers.count(True), 1)


class ReviewScanSaysItOnceTests(unittest.TestCase):
    def _service(self, review_tasks, repository_service=None):
        task_service = MagicMock()
        task_service.get_review_tasks.return_value = review_tasks
        service = ReviewCommentService(
            task_service=task_service, implementation_service=MagicMock(),
            repository_service=repository_service or MagicMock(), state_registry=MagicMock(),
        )
        service.logger = MagicMock()
        return service

    def test_the_forgotten_tasks_line_is_said_when_the_set_changes(self) -> None:
        tasks = [types.SimpleNamespace(id='UNA-1'), types.SimpleNamespace(id='UNA-2')]
        service = self._service(tasks)
        forgotten = {'UNA-1'}
        with patch('kato_core_lib.data_layers.service.review_comment_service.forgotten_task_ids',
                   side_effect=lambda: set(forgotten)), \
             patch.object(service, '_review_task_pull_request_contexts', return_value=[]):
            for _ in range(5):
                service._review_pull_request_contexts()
            forgotten.add('UNA-2')
            service._review_pull_request_contexts()
            service._review_pull_request_contexts()
        lines = [c for c in service.logger.info.call_args_list if 'forgotten task(s)' in str(c)]
        self.assertEqual(len(lines), 2)  # once for {UNA-1}, once for {UNA-1, UNA-2}

    def test_a_comment_read_failure_is_one_warning_not_a_traceback_every_scan(self) -> None:
        repositories = MagicMock()
        repositories.list_pull_request_comments.side_effect = RuntimeError(
            '403 Client Error: Forbidden for url: https://api.bitbucket.org/2.0/repositories/a/b/pullrequests/5/comments',
        )
        service = self._service([], repositories)
        for _ in range(4):
            self.assertEqual(service._pull_request_comments('b', '5'), [])
        service.logger.exception.assert_not_called()
        self.assertEqual(service.logger.warning.call_count, 1)
        self.assertIn('read:pullrequest:bitbucket', ' '.join(map(str, service.logger.warning.call_args.args)))
        repositories.list_pull_request_comments.side_effect = None
        repositories.list_pull_request_comments.return_value = []
        service._pull_request_comments('b', '5')
        service._pull_request_comments('b', '5')
        again = [c for c in service.logger.info.call_args_list if 'can be read again' in str(c)]
        self.assertEqual(len(again), 1)


if __name__ == '__main__':
    unittest.main()

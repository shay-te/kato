import types
import unittest
from unittest.mock import Mock

from kato_core_lib.data_layers.service.implementation_service import (
    ImplementationService,
)
from tests.utils import build_task


class ImplementationServiceTests(unittest.TestCase):
    def test_passes_kato_client_calls(self) -> None:
        client = types.SimpleNamespace(
            implement_task=Mock(),
            fix_review_comment=Mock(),
        )
        service = ImplementationService(client)
        service.logger = Mock()
        task = build_task()
        comment = types.SimpleNamespace(pull_request_id='17', comment_id='99')

        service.implement_task(task, 'conversation-1')
        service.fix_review_comment(comment, 'feature/proj-1', 'conversation-1')

        service.logger.info.assert_any_call('delegating implementation for task %s', 'PROJ-1')
        client.implement_task.assert_called_once_with(
            task,
            'conversation-1',
            prepared_task=None,
        )
        client.fix_review_comment.assert_called_once_with(
            comment,
            'feature/proj-1',
            'conversation-1',
            task_id='',
            task_summary='',
            additional_dirs=None,
        )



class ImplementationServiceInvestigateTests(unittest.TestCase):
    """The one fresh read-only turn a feature like the review loop runs."""

    def test_forwards_every_argument_to_the_client(self) -> None:
        import threading

        class _RecordingClient(object):
            def __init__(self) -> None:
                self.calls: list[tuple] = []

            def investigate(self, prompt, **kwargs):
                self.calls.append((prompt, kwargs))
                return 'report'

        client = _RecordingClient()
        service = ImplementationService(client)
        cancel = threading.Event()
        text = service.investigate(
            'review', cwd='/w/T/api', additional_dirs=['/w/T'],
            sandbox_root='/w/T', task_id='T', log_label='review', cancel_event=cancel,
        )
        self.assertEqual(text, 'report')
        self.assertEqual(client.calls, [('review', dict(
            cwd='/w/T/api', additional_dirs=['/w/T'], sandbox_root='/w/T',
            task_id='T', log_label='review', cancel_event=cancel,
        ))])
        self.assertTrue(service.supports_investigation)

    def test_a_backend_without_it_is_refused_with_a_reason(self) -> None:
        service = ImplementationService(object())
        self.assertFalse(service.supports_investigation)
        with self.assertRaisesRegex(RuntimeError, 'read-only investigation'):
            service.investigate('review')

if __name__ == '__main__':
    unittest.main()

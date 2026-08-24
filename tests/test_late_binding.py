import unittest
from unittest.mock import MagicMock, Mock

from kato_core_lib.helpers.late_binding import call_later, later, provider_for
from kato_core_lib.helpers.lesson_candidate_utils import (
    task_lesson_candidate_id,
    task_lesson_candidate_prefix,
)


class _Host(object):
    def __init__(self) -> None:
        self.collaborator = 'first'

    def greet(self, name: str) -> str:
        return f'hello {name}'


class ProviderForTests(unittest.TestCase):
    def test_a_plain_value_is_returned_unchanged(self) -> None:
        value = object()

        self.assertIs(provider_for(value)(), value)

    def test_none_is_a_value_not_a_missing_collaborator(self) -> None:
        self.assertIsNone(provider_for(None)())

    def test_a_mock_is_a_value_even_though_it_is_callable(self) -> None:
        # The whole reason this module exists: a Mock is callable, so a
        # "callable means getter" rule handed every test a child mock
        # instead of the mock it injected — silently.
        mock = MagicMock()

        self.assertIs(provider_for(mock)(), mock)
        mock.assert_not_called()

    def test_a_later_marker_resolves_on_every_call(self) -> None:
        host = _Host()
        provider = provider_for(later(host, 'collaborator'))

        self.assertEqual(provider(), 'first')
        host.collaborator = 'second'
        self.assertEqual(provider(), 'second')

    def test_a_later_marker_is_itself_callable(self) -> None:
        host = _Host()

        self.assertEqual(later(host, 'collaborator')(), 'first')

    def test_a_later_marker_raises_for_an_attribute_the_host_lacks(self) -> None:
        with self.assertRaises(AttributeError):
            provider_for(later(_Host(), 'nope'))()


class CallLaterTests(unittest.TestCase):
    def test_looks_the_method_up_at_call_time(self) -> None:
        host = _Host()
        call = call_later(host, 'greet')

        self.assertEqual(call('world'), 'hello world')
        host.greet = lambda name: f'bye {name}'
        self.assertEqual(call('world'), 'bye world')

    def test_forwards_positional_and_keyword_arguments(self) -> None:
        host = Mock()
        host.run.return_value = 'done'

        self.assertEqual(call_later(host, 'run')(1, flag=True), 'done')
        host.run.assert_called_once_with(1, flag=True)

    def test_names_itself_after_the_method_for_readable_tracebacks(self) -> None:
        self.assertEqual(call_later(_Host(), '_private').__name__, 'private')


class LessonCandidateIdTests(unittest.TestCase):
    def test_prefix_carries_the_task_id(self) -> None:
        self.assertEqual(task_lesson_candidate_prefix('UNA-1'), 'task__UNA-1__')

    def test_prefix_normalizes_padding_and_none(self) -> None:
        self.assertEqual(task_lesson_candidate_prefix('  UNA-1 '), 'task__UNA-1__')
        self.assertEqual(task_lesson_candidate_prefix(None), 'task____')

    def test_id_starts_with_the_prefix_and_names_its_source(self) -> None:
        candidate_id = task_lesson_candidate_id('UNA-1', 'prompt')

        self.assertTrue(candidate_id.startswith('task__UNA-1__prompt__'))

    def test_id_defaults_the_source_to_prompt(self) -> None:
        self.assertTrue(task_lesson_candidate_id('UNA-1', '').startswith(
            'task__UNA-1__prompt__',
        ))

    def test_ids_are_unique_per_call(self) -> None:
        first = task_lesson_candidate_id('UNA-1', 'prompt')
        second = task_lesson_candidate_id('UNA-1', 'prompt')

        self.assertNotEqual(first, second)


if __name__ == '__main__':
    unittest.main()

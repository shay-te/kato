"""A plan sub-task's question, put to the chat its plan was split in.

The chat is a scripted ``ChatQuestion`` stand-in, and the background thread is
run by hand, so every step is observed in order.
"""

from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace

from kato_core_lib.data_layers.service.chat_question import ChatQuestionCancelled
from kato_core_lib.data_layers.service.plan_queue.parent_question import (
    MAX_QUESTIONS,
    ParentQuestionError,
    ParentQuestions,
    questions_text,
)
from kato_core_lib.helpers.plan_queue_guidance import PLAN_QUESTION_CHAT_HEADER

LINK = {
    'header': 'Link lookup', 'question': 'How should I proceed?', 'multiSelect': False,
    'options': [
        {'label': "I'll add the clone", 'description': 'You add the core lib.'},
        {'label': 'Org code only'},
    ],
}


class Queue(object):
    def __init__(self, plans=None) -> None:
        self.plans = plans or {}

    def plan_for_task(self, task_id):
        source = self.plans.get(task_id)
        return SimpleNamespace(source_task_id=source) if source else None


class ScriptedQuestion(object):
    def __init__(self, answer='Take the org code: the plan kept the backend as is.', error=None) -> None:
        self.answer = answer
        self.error = error
        self.asked = []

    def ask(self, task_id, message, *, cancel_event):
        self.asked.append((task_id, message, cancel_event))
        if self.error is not None:
            raise self.error
        return self.answer


class QuestionsTextTests(unittest.TestCase):
    def test_the_agents_questions_read_as_plain_text(self) -> None:
        text = questions_text([LINK, {'question': 'Which per-IP key?', 'multiSelect': True,
                                      'options': [{'label': ''}, 'odd', {'label': 'Shared bucket'}]}])
        self.assertEqual(text, (
            "1. [Link lookup] How should I proceed?\n"
            "   - I'll add the clone — You add the core lib.\n"
            "   - Org code only\n\n"
            "2. Which per-IP key?\n"
            "   - Shared bucket\n"
            "   (more than one may be chosen)"
        ))

    def test_what_cannot_be_read_is_refused(self) -> None:
        cases = [None, [], [LINK] * (MAX_QUESTIONS + 1), ['a string'], [{'question': '  '}],
                 [{'question': 42}]]
        for questions in cases:
            with self.subTest(questions=questions), self.assertRaises(ParentQuestionError) as caught:
                questions_text(questions)
            self.assertEqual(caught.exception.kind, 'invalid')


class ParentQuestionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.started = []
        self.question = ScriptedQuestion()
        self.service = self._service(self.question)

    def _service(self, question, plans=None):
        return ParentQuestions(
            queue=Queue(plans or {'UNA-3274': 'UNA-3267'}), question=question,
            clock=lambda: 50.0, start=self.started.append,
        )

    def _run(self) -> None:
        self.started.pop(0)()

    def test_the_parent_is_the_task_the_plan_was_split_from(self) -> None:
        self.assertEqual(self.service.parent_of('UNA-3274'), 'UNA-3267')
        self.assertEqual(self.service.parent_of('UNA-9'), '')
        self.assertEqual(ParentQuestions(queue=None, question=self.question).parent_of('UNA-3274'), '')

    def test_asked_in_the_parents_chat_and_the_answer_kept_beside_the_question(self) -> None:
        self.assertIsNone(self.service.state('UNA-3274'))
        asking = self.service.ask('UNA-3274', [LINK])
        self.assertEqual(asking, {'parent': 'UNA-3267', 'status': 'asking', 'answer': '', 'error': '',
                                  'asked_at': 50.0})
        self._run()
        [(task_id, message, _)] = self.question.asked
        self.assertEqual(task_id, 'UNA-3267')
        self.assertTrue(message.startswith(PLAN_QUESTION_CHAT_HEADER.format(task_id='UNA-3274')))
        self.assertIn('1. [Link lookup] How should I proceed?', message)
        self.assertIn('Do not change any files', message)
        answered = self.service.state('UNA-3274')
        self.assertEqual((answered['status'], answered['answer']),
                         ('answered', 'Take the org code: the plan kept the backend as is.'))

    def test_a_task_no_plan_knows_cannot_ask(self) -> None:
        with self.assertRaises(ParentQuestionError) as caught:
            self.service.ask('UNA-9', [LINK])
        self.assertEqual(caught.exception.kind, 'not_found')
        self.assertEqual(self.started, [])

    def test_one_question_at_a_time(self) -> None:
        self.service.ask('UNA-3274', [LINK])
        with self.assertRaises(ParentQuestionError) as caught:
            self.service.ask('UNA-3274', [LINK])
        self.assertEqual(caught.exception.kind, 'conflict')
        self._run()
        self.service.ask('UNA-3274', [LINK])  # answered: it may be asked again
        self.assertEqual(len(self.started), 1)

    def test_why_there_is_no_answer_is_kept(self) -> None:
        for error, shown in [(RuntimeError('kato is running this task'), 'kato is running this task'),
                             (TimeoutError(), 'TimeoutError')]:
            with self.subTest(shown):
                service = self._service(ScriptedQuestion(error=error))
                service.ask('UNA-3274', [LINK])
                self._run()
                failed = service.state('UNA-3274')
                self.assertEqual((failed['status'], failed['error']), ('failed', shown))

    def test_cancel_stops_waiting_at_once_and_a_late_answer_changes_nothing(self) -> None:
        self.assertIsNone(self.service.cancel('UNA-3274'))  # nothing asked
        self.service.ask('UNA-3274', [LINK])
        self.assertEqual(self.service.cancel('UNA-3274')['status'], 'cancelled')
        late = self.started.pop(0)
        self.service.ask('UNA-3274', [LINK])  # asked again at once
        late()  # the first question's answer arrives after all
        self.assertEqual(self.service.state('UNA-3274')['status'], 'asking')
        self._run()
        self.assertEqual(self.service.state('UNA-3274')['status'], 'answered')
        self.assertTrue(self.question.asked[0][2].is_set())  # the first wait was told to stop

    def test_shutdown_stops_every_wait(self) -> None:
        cancelled = ScriptedQuestion(error=ChatQuestionCancelled('stopped'))
        service = self._service(cancelled)
        service.ask('UNA-3274', [LINK])
        service.shutdown()
        self._run()
        self.assertTrue(cancelled.asked[0][2].is_set())
        self.assertEqual(service.state('UNA-3274')['status'], 'cancelled')


class DefaultThreadTests(unittest.TestCase):
    def test_the_question_waits_on_its_own_thread(self) -> None:
        done = threading.Event()

        class Answering(ScriptedQuestion):
            def ask(self, task_id, message, *, cancel_event):
                done.set()
                return 'ok'

        service = ParentQuestions(queue=Queue({'T-2': 'T-1'}), question=Answering())
        service.ask('T-2', [LINK])
        self.assertTrue(done.wait(5))


if __name__ == '__main__':
    unittest.main()

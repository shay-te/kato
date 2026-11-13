"""The per-turn result reducer's error and message extraction, branch by branch.

Pure functions over one event's dict: the shapes below are the ones per-turn
CLIs were seen to emit (a top-level message, a nested ``error``, an ``item``,
an API envelope stringified inside the message).
"""

from __future__ import annotations

import unittest

from agent_core_lib.agent_core_lib.session.turn_result import (
    absorb_agent_message,
    absorb_error,
    absorb_session_id,
    extract_error_text,
    parse_json_event,
    readable_message_from_envelope,
    turn_result,
    unwrap_backend_error_envelope,
)


class ExtractErrorTextTests(unittest.TestCase):
    def test_top_level_message_wins(self) -> None:
        self.assertEqual(extract_error_text({'message': 'top'}), 'top')

    def test_nested_dict_error_message(self) -> None:
        self.assertEqual(
            extract_error_text({'error': {'message': 'nested'}}),
            'nested',
        )

    def test_nested_dict_error_error_fallback(self) -> None:
        # When ``error`` dict has only an ``error`` key (no message)
        self.assertEqual(
            extract_error_text({'error': {'error': 'deeper'}}),
            'deeper',
        )

    def test_nested_string_error(self) -> None:
        self.assertEqual(extract_error_text({'error': 'just a string'}),
                         'just a string')

    def test_item_text_fallback(self) -> None:
        self.assertEqual(
            extract_error_text({'item': {'text': 'from item'}}),
            'from item',
        )

    def test_item_message_fallback(self) -> None:
        self.assertEqual(
            extract_error_text({'item': {'message': 'item msg'}}),
            'item msg',
        )

    def test_returns_empty_when_nothing_found(self) -> None:
        self.assertEqual(extract_error_text({'type': 'error'}), '')

    def test_non_string_message_ignored(self) -> None:
        # If ``message`` is a non-string (e.g. a number or dict), the
        # extractor must skip it and look further.
        self.assertEqual(extract_error_text({'message': 42, 'error': 'real'}), 'real')


class UnwrapBackendErrorEnvelopeTests(unittest.TestCase):
    def test_returns_input_when_not_json(self) -> None:
        self.assertEqual(unwrap_backend_error_envelope('plain text'), 'plain text')

    def test_returns_input_when_invalid_json(self) -> None:
        self.assertEqual(unwrap_backend_error_envelope('{not json'),
                         '{not json')

    def test_returns_input_when_not_a_dict(self) -> None:
        # ``json.loads('[1,2]')`` returns a list, not a dict.
        self.assertEqual(unwrap_backend_error_envelope('[1, 2]'), '[1, 2]')

    def test_unwraps_nested_error_message(self) -> None:
        envelope = '{"error": {"message": "the real one"}}'
        self.assertEqual(unwrap_backend_error_envelope(envelope), 'the real one')

    def test_falls_back_to_top_level_message_when_no_nested_error(self) -> None:
        envelope = '{"message": "top-level only"}'
        self.assertEqual(unwrap_backend_error_envelope(envelope),
                         'top-level only')

    def test_falls_back_to_input_when_envelope_has_no_readable_message(self) -> None:
        envelope = '{"type": "error", "status": 500}'
        # Nothing readable inside — return the original envelope so the
        # operator at least sees something.
        self.assertEqual(unwrap_backend_error_envelope(envelope), envelope)


class ReadableMessageFromEnvelopeTests(unittest.TestCase):
    def test_nested_message_wins(self) -> None:
        self.assertEqual(
            readable_message_from_envelope({'error': {'message': 'hi'}}),
            'hi',
        )

    def test_nested_error_string(self) -> None:
        self.assertEqual(
            readable_message_from_envelope({'error': {'error': 'fallback'}}),
            'fallback',
        )

    def test_top_level_message(self) -> None:
        self.assertEqual(
            readable_message_from_envelope({'message': 'top'}),
            'top',
        )

    def test_returns_empty_when_no_string_anywhere(self) -> None:
        self.assertEqual(readable_message_from_envelope({'status': 500}), '')


class AbsorbAgentMessageBlankTextTests(unittest.TestCase):
    """Line 1277->exit: ``if text:`` falsy — agent_message event with
    blank text leaves payload untouched."""

    def test_blank_text_does_not_overwrite_result(self) -> None:
        payload: dict[str, object] = {'result': 'previous'}
        absorb_agent_message(
            {
                'type': 'item.completed',
                'item': {'type': 'agent_message', 'text': ''},
            },
            payload,
        )
        # Empty text — branch into write skipped, prior value kept.
        self.assertEqual(payload['result'], 'previous')

    def test_whitespace_text_is_treated_as_empty(self) -> None:
        payload: dict[str, object] = {}
        absorb_agent_message(
            {
                'type': 'item.completed',
                'item': {'type': 'agent_message', 'text': ''},
            },
            payload,
        )
        self.assertNotIn('result', payload)


class AbsorbErrorBlankExtractTests(unittest.TestCase):
    """Line 1303->exit: ``if err_text:`` falsy — error event with no
    extractable text still marks payload as error but doesn't write a
    ``result`` field."""

    def test_error_event_without_extractable_text_marks_only_is_error(self) -> None:
        payload: dict[str, object] = {}
        # type=='error' triggers is_error_event but the event has no
        # message/error/item shape → extract_error_text returns ''.
        absorb_error({'type': 'error'}, payload)
        self.assertTrue(payload['is_error'])
        self.assertNotIn('result', payload)


class ExtractErrorTextLoopExitTests(unittest.TestCase):
    """Lines 1314->1320 and 1322->1326: for-loops exit naturally when
    neither candidate key carries a usable string."""

    def test_nested_dict_without_message_or_error_falls_through_to_item(self) -> None:
        # nested is a dict, but neither 'message' nor 'error' key has a
        # non-empty string. The for-loop on line 1314 completes; we
        # then continue to the ``item`` fallback path on line 1320.
        event = {
            'error': {'message': '', 'error': 42, 'unrelated': 'x'},
            'item': {'text': 'recovered from item'},
        }
        self.assertEqual(
            extract_error_text(event), 'recovered from item',
        )

    def test_item_dict_without_text_or_message_returns_empty(self) -> None:
        # item is a dict but neither 'text' nor 'message' has a
        # non-empty string. Loop on line 1322 completes; line 1326
        # returns ''.
        event = {'item': {'other': 'value', 'text': '', 'message': 0}}
        self.assertEqual(extract_error_text(event), '')


class ReadableMessageFromEnvelopeLoopExitTests(unittest.TestCase):
    """Line 1359->1363: nested dict present but with no usable string
    under 'message'/'error' — for-loop exits to the top-level fallback."""

    def test_nested_dict_falls_through_to_top_level_message(self) -> None:
        envelope = {
            'error': {'message': '', 'error': 42},
            'message': 'top level wins',
        }
        self.assertEqual(
            readable_message_from_envelope(envelope), 'top level wins',
        )


class TurnResultTests(unittest.TestCase):
    def test_lines_parse_to_events_or_nothing(self) -> None:
        self.assertEqual(parse_json_event('{"type": "x"}'), {'type': 'x'})
        for line in ('', '   ', 'banner', '{broken', None):
            self.assertIsNone(parse_json_event(line))

    def test_a_run_reduces_to_its_id_last_reply_and_failure(self) -> None:
        result = turn_result([
            {'type': 'thread.started', 'thread_id': 'first'},
            {'session_id': 'second'},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'one'}},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'two'}},
            {'type': 'item.completed', 'item': {'type': 'reasoning', 'text': 'hidden'}},
            {'type': 'item.completed', 'item': 'odd'},
        ])
        self.assertEqual(result, {'agent_session_id': 'first', 'is_error': False, 'result': 'two'})
        failed = turn_result([{'type': 'turn.failed', 'error': {'message': 'quota'}}])
        self.assertEqual((failed['is_error'], failed['result']), (True, 'quota'))

    def test_a_session_id_can_arrive_under_either_key(self) -> None:
        payload = {'agent_session_id': ''}
        absorb_session_id({'session_id': ' s1 '}, payload)
        self.assertEqual(payload['agent_session_id'], 's1')
        absorb_session_id({}, {'agent_session_id': ''})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

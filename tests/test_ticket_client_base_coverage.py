"""Coverage for ``TicketClientBase`` defensive branches.

The base class defines an abstract contract for ticket-platform
clients. Tests below subclass it minimally and exercise the
fall-through branches (NotImplementedError bodies, skip-when-non-dict
loops, etc.) so a regression that breaks the contract is detected.
"""

from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kato_core_lib.client.ticket_client_base import TicketClientBase


def _client() -> TicketClientBase:
    """Build a TicketClientBase instance bypassing the network init."""

    client = TicketClientBase.__new__(TicketClientBase)
    client.logger = logging.getLogger('test_ticket_client_base')
    client.provider_name = 'test'
    return client


class TicketClientBaseInterfaceTests(unittest.TestCase):
    """Lines 68-78: every interface method raises NotImplementedError
    by default. Subclasses must override."""

    def test_validate_connection_raises_not_implemented(self) -> None:
        with self.assertRaises(NotImplementedError):
            _client().validate_connection('p', 'me', ['Open'])

    def test_get_assigned_tasks_raises_not_implemented(self) -> None:
        with self.assertRaises(NotImplementedError):
            _client().get_assigned_tasks('p', 'me', ['Open'])

    def test_add_comment_raises_not_implemented(self) -> None:
        with self.assertRaises(NotImplementedError):
            _client().add_comment('PROJ-1', 'body')

    def test_move_issue_to_state_raises_not_implemented(self) -> None:
        with self.assertRaises(NotImplementedError):
            _client().move_issue_to_state('PROJ-1', 'State', 'Open')


class TicketClientBaseTagFallbackTests(unittest.TestCase):
    """Lines 91-95: ``add_tag`` / ``remove_tag`` default to posting
    a structured marker comment so the change is visible in the
    activity log even when the platform doesn't have native tags."""

    def test_add_tag_posts_marker_comment(self) -> None:
        client = _client()
        with patch.object(client, 'add_comment') as add_comment:
            client.add_tag('PROJ-1', 'urgent')
        add_comment.assert_called_once()
        body = add_comment.call_args.args[1]
        self.assertIn('Kato added tag', body)
        self.assertIn('urgent', body)
        self.assertIn('<!-- kato-tag', body)

    def test_remove_tag_posts_marker_comment(self) -> None:
        client = _client()
        with patch.object(client, 'add_comment') as add_comment:
            client.remove_tag('PROJ-1', 'urgent')
        body = add_comment.call_args.args[1]
        self.assertIn('Kato removeed tag', body)
        self.assertIn('"action": "remove"', body)


class TicketClientBaseCommentLinesTests(unittest.TestCase):
    """Line 144: ``if not isinstance(comment, dict): continue`` in
    ``_comment_lines`` — non-dict entries are skipped silently."""

    def test_comment_lines_skips_non_dict_entries(self) -> None:
        lines = TicketClientBase._comment_lines([
            'not a dict',
            42,
            {'body': 'real comment', 'author': 'alice'},
        ])
        self.assertEqual(len(lines), 1)
        self.assertIn('alice', lines[0])


class JsonItemsTests(unittest.TestCase):
    """Line 221: ``if not isinstance(payload, dict): return []`` —
    when ``items_key`` is set but the response is not a dict."""

    def test_returns_empty_when_response_is_list_but_items_key_set(self) -> None:
        response = MagicMock()
        response.json.return_value = ['list', 'not', 'a', 'dict']
        self.assertEqual(
            TicketClientBase._json_items(response, items_key='items'), [],
        )

    def test_returns_empty_when_payload_lacks_items_key(self) -> None:
        # Returns the bare list when items_key isn't present (line 223).
        response = MagicMock()
        response.json.return_value = {'items': 'not a list'}
        self.assertEqual(
            TicketClientBase._json_items(response, items_key='items'), [],
        )


class NormalizeIssueTasksTests(unittest.TestCase):
    """Line 303: ``include`` filter callback skips matching items."""

    def test_skips_items_where_include_returns_false(self) -> None:
        # Drives ``if include and not include(item): continue``.
        client = _client()
        result = client._normalize_issue_tasks(
            [{'id': 'PROJ-1'}, {'id': 'PROJ-2'}],
            to_task=lambda d: SimpleNamespace(id=d['id']),
            include=lambda d: d.get('id') != 'PROJ-2',
        )
        self.assertEqual([t.id for t in result], ['PROJ-1'])


class ResponseItemsTests(unittest.TestCase):
    """Lines 316-318: ``_response_items`` thin wrapper that calls
    _get_with_retry, raises for HTTP error, returns json items."""

    def test_passes_through_json_items(self) -> None:
        client = _client()
        response = MagicMock()
        response.json.return_value = [{'id': 1}, {'id': 2}]
        response.raise_for_status = MagicMock()
        with patch.object(client, '_get_with_retry', return_value=response):
            result = client._response_items('/api/items')
        self.assertEqual(result, [{'id': 1}, {'id': 2}])
        response.raise_for_status.assert_called_once()


class ActiveAgentBlockingCommentTests(unittest.TestCase):
    """Lines 445, 448: the loop skips non-dict entries (line 445) and
    skips entries with blank body (line 448)."""

    def test_skips_non_dict_and_blank_body_entries(self) -> None:
        # Use a real blocking prefix from PRE_START_BLOCKING_PREFIXES.
        blocking_body = (
            'Kato agent could not safely process this task: '
            'repo not found.'
        )
        comments = [
            'not a dict',            # line 445: skipped
            {'body': ''},            # line 448: skipped
            {'body': blocking_body},
        ]
        active = TicketClientBase._active_agent_blocking_comment(
            comments, TicketClientBase.PRE_START_BLOCKING_PREFIXES,
        )
        self.assertIn('repo not found', active)


class BestEffortIssueResponseItemsTests(unittest.TestCase):
    """Line 303: ``_best_effort_issue_response_items`` thin wrapper."""

    def test_returns_response_items_on_success(self) -> None:
        client = _client()
        with patch.object(
            client, '_response_items', return_value=[{'id': 1}],
        ):
            result = client._best_effort_issue_response_items(
                'PROJ-1', item_label='attachments', path='/api/x',
            )
        self.assertEqual(result, [{'id': 1}])

    def test_returns_empty_on_exception(self) -> None:
        # Best-effort wrapper logs + returns []. Drives line 303 path.
        client = _client()
        with patch.object(
            client, '_response_items',
            side_effect=RuntimeError('network fail'),
        ):
            result = client._best_effort_issue_response_items(
                'PROJ-1', item_label='attachments', path='/api/x',
            )
        self.assertEqual(result, [])


if __name__ == '__main__':
    unittest.main()

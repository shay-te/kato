"""A rejected search must carry the server's explanation, not just the URL.

Reported from a first-time setup: validation failed with

    400 Client Error: Bad Request for url: .../api/issues?query=project%3A+...

and nothing else. The server had answered with a body naming the cause; bare
``raise_for_status()`` threw it away, leaving a percent-encoded query to
decode by hand and no statement of what was wrong with it.
"""

from __future__ import annotations

import unittest

import requests

from youtrack_core_lib.youtrack_core_lib.client.youtrack_client_base import (
    raise_for_status_with_detail,
)


class _Response:
    """Minimal stand-in with the two attributes the helper reads."""

    def __init__(self, status_code: int, text: str, url: str = 'https://host/api/issues'):
        self.status_code = status_code
        self.text = text
        self.url = url

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(
                f'{self.status_code} Client Error: Bad Request for url: {self.url}',
                response=self,
            )


class RaiseForStatusWithDetailTests(unittest.TestCase):

    def test_a_success_passes_through_silently(self) -> None:
        raise_for_status_with_detail(_Response(200, '{"ok": true}'))

    def test_the_server_explanation_reaches_the_message(self) -> None:
        body = '{"error":"Bad Request","error_description":"Unknown field: State"}'
        with self.assertRaises(requests.HTTPError) as raised:
            raise_for_status_with_detail(_Response(400, body))

        message = str(raised.exception)
        # The original text is kept...
        self.assertIn('400 Client Error', message)
        # ...and the part that actually says what to fix is now present.
        self.assertIn('Unknown field: State', message)

    def test_the_original_error_type_is_preserved(self) -> None:
        # Callers upstream catch requests exceptions by type; re-raising as
        # something else would slip past them.
        with self.assertRaises(requests.HTTPError):
            raise_for_status_with_detail(_Response(400, 'nope'))

    def test_the_cause_is_chained(self) -> None:
        with self.assertRaises(requests.HTTPError) as raised:
            raise_for_status_with_detail(_Response(500, 'boom'))
        self.assertIsInstance(raised.exception.__cause__, requests.HTTPError)

    def test_an_empty_body_re_raises_the_original_untouched(self) -> None:
        with self.assertRaises(requests.HTTPError) as raised:
            raise_for_status_with_detail(_Response(400, ''))
        self.assertNotIn('Response:', str(raised.exception))

    def test_a_long_body_is_truncated(self) -> None:
        # An HTML error page must not bury the rest of the message.
        with self.assertRaises(requests.HTTPError) as raised:
            raise_for_status_with_detail(_Response(502, '<html>' + 'x' * 5000))
        message = str(raised.exception)
        self.assertLess(len(message), 1000)
        self.assertIn('…', message)

    def test_a_response_without_text_still_raises(self) -> None:
        class _NoText:
            status_code = 400

            def raise_for_status(self):
                raise requests.HTTPError('400 Client Error')

        with self.assertRaises(requests.HTTPError):
            raise_for_status_with_detail(_NoText())


if __name__ == '__main__':
    unittest.main()

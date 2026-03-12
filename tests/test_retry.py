import unittest

import bootstrap  # noqa: F401

from openhands_agent.client.retry_utils import (
    is_retryable_exception,
    is_retryable_response,
)


class ConnectTimeout(Exception):
    pass


class PermanentFailure(Exception):
    pass


class RetryTests(unittest.TestCase):
    def test_is_retryable_exception_accepts_known_timeout_names(self) -> None:
        self.assertTrue(is_retryable_exception(ConnectTimeout('timeout')))
        self.assertTrue(is_retryable_exception(TimeoutError('timeout')))

    def test_is_retryable_exception_rejects_non_transient_errors(self) -> None:
        self.assertFalse(is_retryable_exception(PermanentFailure('bad request')))
        self.assertFalse(is_retryable_exception(ValueError('bad value')))

    def test_is_retryable_response_accepts_transient_status_codes(self) -> None:
        self.assertTrue(is_retryable_response(type('Response', (), {'status_code': 503})()))
        self.assertTrue(is_retryable_response(type('Response', (), {'status_code': 429})()))

    def test_is_retryable_response_rejects_non_transient_status_codes(self) -> None:
        self.assertFalse(is_retryable_response(type('Response', (), {'status_code': 400})()))
        self.assertFalse(is_retryable_response(type('Response', (), {})()))

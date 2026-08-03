import unittest
from unittest.mock import Mock, patch


from provider_client_base.provider_client_base.helpers.retry_utils import (
    _retry_delay_seconds,
    _service_name_from_client_name,
    is_retryable_exception,
    is_retryable_response,
    run_with_retry,
)
# retry_count is a CONFIG clamp, not retry-engine code — it moved to the
# config helper when kato's stale fork of the engine was deleted.
from kato_core_lib.helpers.kato_config_utils import retry_count


class ConnectTimeout(Exception):
    pass


class PermanentFailure(Exception):
    pass


class RemoteDisconnected(Exception):
    pass


class RetryTests(unittest.TestCase):
    def test_retry_count_enforces_minimum_one(self) -> None:
        self.assertEqual(retry_count(5), 5)
        self.assertEqual(retry_count(0), 1)
        self.assertEqual(retry_count(-3), 1)

    def test_retry_count_falls_back_to_default_for_invalid_values(self) -> None:
        self.assertEqual(retry_count('bad', default=4), 4)
        self.assertEqual(retry_count(None, default=0), 1)

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

    def test_run_with_retry_sleeps_before_retrying_exceptions(self) -> None:
        operation = Mock(side_effect=[ConnectTimeout('timeout'), 'ok'])

        with patch('provider_client_base.provider_client_base.helpers.retry_utils.random.uniform', return_value=1.5) as mock_uniform, patch(
            'provider_client_base.provider_client_base.helpers.retry_utils.time.sleep'
        ) as mock_sleep:
            result = run_with_retry(operation, 2)

        self.assertEqual(result, 'ok')
        mock_uniform.assert_called_once_with(1.0, 2.0)
        mock_sleep.assert_called_once_with(1.5)

    def test_run_with_retry_logs_readable_exception_retry_message(self) -> None:
        operation = Mock(side_effect=[ConnectTimeout('timeout'), 'ok'])

        with patch('provider_client_base.provider_client_base.helpers.retry_utils.random.uniform', return_value=1.5), patch(
            'provider_client_base.provider_client_base.helpers.retry_utils.time.sleep'
        ), patch(
            'provider_client_base.provider_client_base.helpers.retry_utils.logger.warning'
        ) as mock_warning:
            result = run_with_retry(
                operation,
                2,
                operation_name='OpenHandsClient GET http://openhands:3000/api/v1/app-conversations/count',
            )

        self.assertEqual(result, 'ok')
        mock_warning.assert_called_once_with(
            '%s connection failed; retrying in %.1fs (attempt %s/%s).\n'
            '%s (%s %s).',
            'OpenHands',
            1.5,
            2,
            2,
            'timeout',
            'GET',
            'http://openhands:3000/api/v1/app-conversations/count',
        )

    def test_run_with_retry_summarizes_remote_disconnects(self) -> None:
        operation = Mock(
            side_effect=[
                ConnectionError(
                    'Connection aborted.',
                    RemoteDisconnected('Remote end closed connection without response'),
                ),
                'ok',
            ]
        )

        with patch('provider_client_base.provider_client_base.helpers.retry_utils.random.uniform', return_value=1.5), patch(
            'provider_client_base.provider_client_base.helpers.retry_utils.time.sleep'
        ), patch(
            'provider_client_base.provider_client_base.helpers.retry_utils.logger.warning'
        ) as mock_warning:
            result = run_with_retry(
                operation,
                5,
                operation_name='YouTrackClient GET https://shay-te.youtrack.cloud/api/issues',
            )

        self.assertEqual(result, 'ok')
        mock_warning.assert_called_once_with(
            '%s connection failed; retrying in %.1fs (attempt %s/%s).\n'
            '%s (%s %s).',
            'YouTrack',
            1.5,
            2,
            5,
            'Remote server closed connection',
            'GET',
            'https://shay-te.youtrack.cloud/api/issues',
        )

    def test_run_with_retry_raises_after_exhausting_all_retries(self) -> None:
        operation = Mock(side_effect=ConnectTimeout('always fails'))

        with patch('provider_client_base.provider_client_base.helpers.retry_utils.time.sleep') as mock_sleep:
            with self.assertRaises(ConnectTimeout):
                run_with_retry(operation, 3)

        self.assertEqual(operation.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)

    def test_service_name_strips_client_suffix(self) -> None:
        # Sanity: the suffix-stripping path is hit by the standard
        # ``FooClient`` naming convention.
        self.assertEqual(_service_name_from_client_name('YouTrackClient'), 'YouTrack')

    def test_service_name_returns_name_unchanged_without_client_suffix(self) -> None:
        # Line 137: ``if normalized_name.endswith('Client'):`` False —
        # caller passed a service name that wasn't suffixed, so the
        # branch must fall through and return the trimmed value as-is.
        # Guards the ``OpenHands`` / ``GitHub`` short-name path.
        self.assertEqual(_service_name_from_client_name('OpenHands'), 'OpenHands')

    def test_service_name_returns_fallback_when_input_blank(self) -> None:
        # The trailing ``or 'Request'`` keeps log messages readable
        # even when the client name was empty or whitespace.
        self.assertEqual(_service_name_from_client_name(''), 'Request')
        self.assertEqual(_service_name_from_client_name('   '), 'Request')

    def test_retry_delay_uses_retry_after_header_for_rate_limits(self) -> None:
        response = type(
            'Response',
            (),
            {'status_code': 429, 'headers': {'Retry-After': '3'}},
        )()

        # Retry-After is honoured, plus bounded jitter so parallel clients
        # told "wait 3s" do not all wake in lockstep and re-trip the limit.
        delay = _retry_delay_seconds(0, response)
        self.assertGreaterEqual(delay, 3.0)
        self.assertLess(delay, 3.0 * 2)

    def test_retry_delay_falls_back_to_exponential_backoff_without_retry_after(self) -> None:
        response = type(
            'Response',
            (),
            {'status_code': 429, 'headers': {}},
        )()

        # 429 with NO Retry-After uses the RATE-LIMIT schedule (15s base),
        # not generic 2**attempt. This assertion previously pinned the generic
        # 4-8s window — a schedule that can never outlast a provider quota
        # window (60-3600s), which is the "PR lookup failed / 429" symptom.
        with patch('provider_client_base.provider_client_base.helpers.retry_utils.random.uniform', return_value=5.5) as mock_uniform:
            self.assertEqual(_retry_delay_seconds(2, response), 5.5)

        mock_uniform.assert_called_once_with(60.0, 90.0)

    def test_retry_delay_falls_back_to_exponential_backoff_for_invalid_retry_after(self) -> None:
        response = type(
            'Response',
            (),
            {'status_code': 429, 'headers': {'Retry-After': 'abc'}},
        )()

        # An unparseable Retry-After falls back to the RATE-LIMIT schedule
        # too, because the response is still a 429.
        with patch('provider_client_base.provider_client_base.helpers.retry_utils.random.uniform', return_value=3.25) as mock_uniform:
            self.assertEqual(_retry_delay_seconds(1, response), 3.25)

        mock_uniform.assert_called_once_with(30.0, 45.0)

    def test_retry_delay_falls_back_to_exponential_backoff_for_non_rate_limited_response(self) -> None:
        response = type(
            'Response',
            (),
            {'status_code': 503, 'headers': {'Retry-After': '3'}},
        )()

        with patch('provider_client_base.provider_client_base.helpers.retry_utils.random.uniform', return_value=1.75) as mock_uniform:
            self.assertEqual(_retry_delay_seconds(0, response), 1.75)

        mock_uniform.assert_called_once_with(1.0, 2.0)

    def test_run_with_retry_logs_readable_retryable_response_message(self) -> None:
        response = type('Response', (), {'status_code': 503})()
        operation = Mock(side_effect=[response, 'ok'])

        with patch('provider_client_base.provider_client_base.helpers.retry_utils.random.uniform', return_value=1.75), patch(
            'provider_client_base.provider_client_base.helpers.retry_utils.time.sleep'
        ), patch(
            'provider_client_base.provider_client_base.helpers.retry_utils.logger.warning'
        ) as mock_warning:
            result = run_with_retry(
                operation,
                2,
                operation_name='GitHubClient POST https://api.github.com/graphql',
            )

        self.assertEqual(result, 'ok')
        mock_warning.assert_called_once_with(
            '%s request returned status %s; retrying in %.1fs (attempt %s/%s).\n'
            'Received retryable response from %s %s.',
            'GitHub',
            503,
            1.75,
            2,
            2,
            'POST',
            'https://api.github.com/graphql',
        )

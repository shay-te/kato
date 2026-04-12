import types
import unittest
from unittest.mock import Mock, patch

from kato.helpers.shell_status_utils import (
    run_with_inline_status_spinner,
    sleep_with_scan_spinner,
    sleep_with_warmup_countdown,
)


class ShellStatusUtilsTests(unittest.TestCase):
    def test_run_with_inline_status_spinner_runs_without_spinner_when_inline_status_is_unavailable(self) -> None:
        operation = Mock(return_value='ok')

        result = run_with_inline_status_spinner(
            operation,
            status_text='Validating connection 1/4: openhands',
            stream=types.SimpleNamespace(isatty=lambda: False),
        )

        self.assertEqual(result, 'ok')
        operation.assert_called_once_with()

    def test_run_with_inline_status_spinner_starts_and_stops_spinner_when_supported(self) -> None:
        operation = Mock(return_value='ok')
        spinner = Mock()

        with patch(
            'kato.helpers.shell_status_utils.InlineStatusSpinner',
            return_value=spinner,
        ) as mock_spinner_cls:
            result = run_with_inline_status_spinner(
                operation,
                status_text='Validating connection 1/4: openhands',
            )

        self.assertEqual(result, 'ok')
        mock_spinner_cls.assert_called_once_with(
            'Validating connection 1/4: openhands',
            stream=None,
        )
        spinner.start.assert_called_once_with()
        spinner.stop.assert_called_once_with()
        operation.assert_called_once_with()

    def test_sleep_with_scan_spinner_uses_plain_sleep_without_tty(self) -> None:
        sleep_fn = Mock()
        stream = types.SimpleNamespace(
            isatty=lambda: False,
            write=Mock(),
            flush=Mock(),
        )

        sleep_with_scan_spinner(3.0, sleep_fn=sleep_fn, stream=stream)

        sleep_fn.assert_called_once_with(3.0)
        stream.write.assert_not_called()
        stream.flush.assert_not_called()

    def test_sleep_with_scan_spinner_updates_inline_status_for_tty_stream(self) -> None:
        sleep_calls: list[float] = []
        status_text = 'Scanning for new tasks and comments'

        class _Stream:
            def __init__(self) -> None:
                self.chunks: list[str] = []

            def isatty(self) -> bool:
                return True

            def write(self, chunk: str) -> None:
                self.chunks.append(chunk)

            def flush(self) -> None:
                return None

        stream = _Stream()

        sleep_with_scan_spinner(
            0.45,
            status_text=status_text,
            sleep_fn=lambda seconds: sleep_calls.append(seconds),
            stream=stream,
        )

        self.assertEqual(sleep_calls, [0.2, 0.2, 0.04999999999999999])
        self.assertEqual(
            stream.chunks,
            [
                '\rScanning for new tasks and comments /',
                '\rScanning for new tasks and comments -',
                '\rScanning for new tasks and comments \\',
                '\r' + (' ' * 40) + '\r',
            ],
        )

    def test_sleep_with_warmup_countdown_updates_inline_status_for_tty_stream(self) -> None:
        sleep_calls: list[float] = []

        class _Stream:
            def __init__(self) -> None:
                self.chunks: list[str] = []

            def isatty(self) -> bool:
                return True

            def write(self, chunk: str) -> None:
                self.chunks.append(chunk)

            def flush(self) -> None:
                return None

        stream = _Stream()

        sleep_with_warmup_countdown(
            1.05,
            sleep_fn=lambda seconds: sleep_calls.append(seconds),
            stream=stream,
        )

        self.assertEqual(sleep_calls[:5], [0.2, 0.2, 0.2, 0.2, 0.2])
        self.assertAlmostEqual(sleep_calls[5], 0.05)
        self.assertEqual(
            stream.chunks,
            [
                '\rWaiting 2 seconds for Kato to warm up before scanning tasks /',
                '\rWaiting 1 second for Kato to warm up before scanning tasks -',
                '\rWaiting 1 second for Kato to warm up before scanning tasks \\',
                '\rWaiting 1 second for Kato to warm up before scanning tasks |',
                '\rWaiting 1 second for Kato to warm up before scanning tasks /',
                '\rWaiting 1 second for Kato to warm up before scanning tasks -',
                '\r'
                + (' ' * (len('Waiting 999 seconds for Kato to warm up before scanning tasks /') + 2))
                + '\r',
            ],
        )

"""Adversarial regression test for streaming.py bug:
``send_permission_response`` pops the request from
``_pending_control_requests`` BEFORE writing to stdin. If the write
fails (broken pipe, dead subprocess), the live state lies:
``pending_control_request_tool()`` returns ``''`` even though
Claude never received the response and is still blocked waiting.

Operator-visible consequence:
    1. Modal vanishes (operator thinks approval went through).
    2. Orange "needs attention" dot clears.
    3. Claude subprocess is stuck on stdin read, forever blocked.
    4. No way to retry — the request_id is gone from kato's state.

The contract this test pins: if the stdin write fails, the request
must REMAIN in ``_pending_control_requests`` so the operator can
retry and the orange-dot signal stays accurate.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch


class BugPermissionPoppedBeforeWriteTests(unittest.TestCase):

    def test_failed_stdin_write_does_not_lose_the_pending_request(self) -> None:
        # Build a minimal StreamingClaudeSession instance without
        # actually spawning a subprocess. We only need:
        #   - _pending_control_requests populated with one entry
        #   - _write_stdin_line forced to raise (simulating dead subprocess)
        #   - then assert the entry is STILL in _pending_control_requests
        from claude_core_lib.claude_core_lib.session.streaming import (
            StreamingClaudeSession,
        )

        session = StreamingClaudeSession.__new__(StreamingClaudeSession)
        # Initialize only what send_permission_response touches.
        import threading
        session._task_id = 'T1'
        session._pending_control_requests_lock = threading.Lock()
        session._pending_control_requests = {
            'req-1': {
                'tool_name': 'Bash',
                'input': {'command': 'ls'},
            },
        }
        # Stub _write_stdin_line to simulate a dead subprocess.
        with patch.object(
            session, '_write_stdin_line',
            side_effect=RuntimeError(
                'streaming session for task T1 stdin broke: BrokenPipeError',
            ),
        ):
            # Stub _publish_event so the test doesn't need the event queue.
            with patch.object(session, '_publish_event'):
                with self.assertRaises(RuntimeError):
                    session.send_permission_response(
                        request_id='req-1', allow=True,
                    )

        # The contract: a failed write must NOT silently drop the
        # request from live state. Otherwise the operator's orange dot
        # clears and they have no signal that Claude is still blocked.
        with session._pending_control_requests_lock:
            tool_name = (
                session._pending_control_requests.get('req-1', {})
                .get('tool_name', '')
            )
        self.assertEqual(
            tool_name, 'Bash',
            'permission request was popped from live state even though the '
            'stdin write failed. Operator sees the orange dot clear, but '
            'Claude is still blocked on stdin waiting. No way to retry — '
            'kato has lost the request entirely.',
        )


if __name__ == '__main__':
    unittest.main()

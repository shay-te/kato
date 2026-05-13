"""Adversarial regression test for session/manager.py bug:
``_died_with_stale_resume_id`` returns True whenever the marker
string is found in stderr, regardless of whether the subprocess is
actually dead. Combined with ``_wait_for_stale_resume_failure`` line
617, this fires the self-heal path on a perfectly healthy session
whose stderr happens to contain the marker.

Trigger: a Claude tool (or Claude itself) emits a log line that
contains the marker text for a benign reason — for example, an
audit-log entry, a debug trace, or any string that happens to read
``No conversation found with session ID: <id>``. The healthy
session is then incorrectly torn down and respawned fresh, burning
tokens and losing context.

The contract: detection of stale resume must require evidence that
the subprocess actually exited because of the resume failure, not
just the presence of the marker text in stderr.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace


class BugStaleResumeFalsePositiveTests(unittest.TestCase):

    def test_alive_session_with_marker_in_stderr_does_not_trigger_self_heal(self) -> None:
        # An alive session (is_alive=True) whose stderr happens to
        # contain the marker text must NOT be classified as
        # "died with stale resume id". Otherwise the self-heal path
        # fires on a healthy session.
        from claude_core_lib.claude_core_lib.session.manager import (
            ClaudeSessionManager,
        )

        alive_session = SimpleNamespace(
            is_alive=True,  # subprocess is still running
            terminal_event=None,  # no terminal event yet
            stderr_snapshot=lambda: [
                'INFO: scanning logs',
                # Marker appears for a benign reason (e.g., Claude
                # itself echoing the env var for debug, or a tool
                # output that happens to mention the session id).
                'No conversation found with session ID: live-session-id',
                'INFO: continuing',
            ],
        )

        result = ClaudeSessionManager._died_with_stale_resume_id(
            alive_session, 'live-session-id',
        )
        self.assertFalse(
            result,
            'detection fired on an ALIVE session because the marker '
            'text appeared in stderr for a benign reason. The self-heal '
            'path will spuriously terminate the session and start fresh, '
            'burning tokens and losing context.',
        )


if __name__ == '__main__':
    unittest.main()

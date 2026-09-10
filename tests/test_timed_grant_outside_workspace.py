"""The out-of-workspace opt-in for time-boxed approvals.

``docker run -v /host/path:/data`` mounts an absolute host path by nature, so
it always reads as out-of-workspace. The auto-resolve path refused every
out-of-workspace ask outright, before it ever reached the timed-grant check —
so even after the operator clicked "Allow for 10 min", the next docker command
prompted again. Reported as: "still i see only allow once on the docker run".

``KATO_TIMED_GRANT_OUTSIDE_WORKSPACE`` opts in. What it must NOT do is the
point of this file: it never widens eligibility, never enables a remembered
("Allow always") approval out of workspace, and never overrides the carve-outs
that follow it in the caller.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from kato_core_lib.helpers.timed_tool_grant_store import (
    clear_timed_grants,
    grant_for,
)
from webserver.kato_webserver.app import _timed_grant_may_cover_outside

ENV = 'KATO_TIMED_GRANT_OUTSIDE_WORKSPACE'
DOCKER = {'command': 'docker run -v /Users/me/data:/data img'}


class TimedGrantOutsideWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_timed_grants()
        self.addCleanup(clear_timed_grants)

    def _on(self):
        return patch.dict(os.environ, {ENV: 'true'})

    def _off(self):
        return patch.dict(os.environ, {ENV: 'false'})

    def test_opted_in_with_a_live_grant_lets_the_ask_through(self) -> None:
        grant_for('Bash', ['docker'], minutes=10)
        with self._on():
            self.assertTrue(_timed_grant_may_cover_outside('Bash', DOCKER))

    def test_switch_off_refuses_even_with_a_live_grant(self) -> None:
        # The default posture. The grant exists; it simply does not reach
        # outside the task folder.
        grant_for('Bash', ['docker'], minutes=10)
        with self._off():
            self.assertFalse(_timed_grant_may_cover_outside('Bash', DOCKER))

    def test_opted_in_but_no_live_grant_still_prompts(self) -> None:
        # The switch is permission to HONOUR a window, not a standing allow.
        with self._on():
            self.assertFalse(_timed_grant_may_cover_outside('Bash', DOCKER))

    def test_an_expired_grant_stops_covering_it(self) -> None:
        grant_for('Bash', ['docker'], minutes=-1)
        with self._on():
            self.assertFalse(_timed_grant_may_cover_outside('Bash', DOCKER))

    def test_it_never_widens_which_commands_are_eligible(self) -> None:
        # A grant cannot even be recorded for ``rm``; assert the gate refuses
        # it regardless of what the operator switched on.
        grant_for('Bash', ['rm'], minutes=10)
        with self._on():
            self.assertFalse(
                _timed_grant_may_cover_outside('Bash', {'command': 'rm -rf /etc'}),
            )

    def test_a_grant_on_docker_does_not_cover_a_chained_command(self) -> None:
        # ``timed_grant_active`` requires EVERY program in the chain to be
        # granted — the switch must not become a way around that.
        grant_for('Bash', ['docker'], minutes=10)
        with self._on():
            self.assertFalse(
                _timed_grant_may_cover_outside(
                    'Bash', {'command': 'docker build . && rm -rf /etc'},
                ),
            )

    def test_the_network_tools_are_covered_too(self) -> None:
        grant_for('WebFetch', [], minutes=10)
        with self._on():
            self.assertTrue(
                _timed_grant_may_cover_outside('WebFetch', {'url': 'https://x'}),
            )


if __name__ == '__main__':
    unittest.main()

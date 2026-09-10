"""Time-boxed tool approvals — "Allow for 10 min".

The middle ground between the two existing buttons. "Allow once" re-asks on
every command, which for a task that runs docker in a loop is a stream of
interruptions the operator stops reading; "Allow always" is a persisted,
global grant, too much to hand over for a burst of work.

The properties that make it safe are the ones under test: narrow eligibility,
per-program keys, real expiry, and nothing on disk.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from kato_core_lib.helpers import timed_tool_grant_store as store


class TimedGrantEligibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        store.clear_timed_grants()
        self.addCleanup(store.clear_timed_grants)

    def test_docker_and_the_network_tools_are_eligible(self) -> None:
        self.assertTrue(store.timed_grant_eligible('Bash', ['docker']))
        self.assertTrue(store.timed_grant_eligible('Bash', ['docker-compose']))
        self.assertTrue(store.timed_grant_eligible('WebFetch', []))
        self.assertTrue(store.timed_grant_eligible('WebSearch', []))

    def test_everything_else_keeps_the_existing_two_choices(self) -> None:
        # Deliberately narrow — this is not a general "stop asking me" switch.
        self.assertFalse(store.timed_grant_eligible('Bash', ['rm']))
        self.assertFalse(store.timed_grant_eligible('Bash', ['npm', 'mvn']))
        self.assertFalse(store.timed_grant_eligible('Edit', []))
        self.assertFalse(store.timed_grant_eligible('', ['docker']))

    def test_a_mixed_chain_is_eligible_but_only_docker_is_granted(self) -> None:
        # The prompt may be offered, but the grant itself only ever covers the
        # eligible program — see the "every program" rule below.
        self.assertEqual(store.grant_for('Bash', ['docker', 'rm']), 1)
        self.assertFalse(store.timed_grant_active('Bash', ['docker', 'rm']))
        self.assertTrue(store.timed_grant_active('Bash', ['docker']))


class TimedGrantLifetimeTests(unittest.TestCase):
    def setUp(self) -> None:
        store.clear_timed_grants()
        self.addCleanup(store.clear_timed_grants)

    def test_a_grant_covers_later_commands_for_the_same_program(self) -> None:
        store.grant_for('Bash', ['docker'])
        self.assertTrue(store.timed_grant_active('Bash', ['docker']))

    def test_a_grant_never_covers_a_different_program(self) -> None:
        # Approving ``docker compose up`` must not silently allow ``rm``.
        store.grant_for('Bash', ['docker'])
        self.assertFalse(store.timed_grant_active('Bash', ['npm']))
        self.assertFalse(store.timed_grant_active('Bash', ['rm']))

    def test_every_program_in_a_chain_must_be_granted(self) -> None:
        # ``docker build … && rm -rf /`` must not ride in on the docker grant.
        store.grant_for('Bash', ['docker'])
        self.assertFalse(store.timed_grant_active('Bash', ['docker', 'rm']))

    def test_it_expires(self) -> None:
        # The whole point: it lapses on its own rather than standing until
        # someone remembers to revoke it.
        store.grant_for('Bash', ['docker'], minutes=-1)
        self.assertFalse(store.timed_grant_active('Bash', ['docker']))

    def test_a_zero_or_negative_window_grants_nothing(self) -> None:
        self.assertEqual(store.grant_for('Bash', ['docker'], minutes=0), 0)
        self.assertFalse(store.timed_grant_active('Bash', ['docker']))

    def test_an_ineligible_program_is_never_granted(self) -> None:
        self.assertEqual(store.grant_for('Bash', ['rm']), 0)
        self.assertFalse(store.timed_grant_active('Bash', ['rm']))

    def test_grants_are_listable_and_revocable(self) -> None:
        store.grant_for('Bash', ['docker'])
        listed = store.active_grants()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]['program'], 'docker')
        self.assertGreater(listed[0]['seconds_remaining'], 0)
        store.clear_timed_grants()
        self.assertEqual(store.active_grants(), [])

    def test_nothing_is_written_to_disk(self) -> None:
        """A forgotten permission must not outlive the process.

        That is the whole difference between this and
        ``tool_decision_store``, which persists to ``~/.kato``. A restart has
        to drop every grant, so the store keeps no file at all.
        """
        import inspect
        source = inspect.getsource(store)
        for persistence in ('open(', 'atomic_write', 'json.dump', 'kato_home_path'):
            self.assertNotIn(
                persistence, source,
                f'timed grants must stay in memory — found {persistence!r}',
            )


if __name__ == '__main__':
    unittest.main()


class OutsideWorkspaceOptInTests(unittest.TestCase):
    """``KATO_TIMED_GRANT_OUTSIDE_WORKSPACE`` — the operator's switch.

    Default OFF made the feature useless for the workload it was built for:
    ``docker run -v /host/path:/data`` mounts an absolute host path by
    nature, so it always reads as out-of-workspace and both durable scopes
    were withheld. "still i see only allow once on the docker run."
    """

    def test_default_is_off(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('KATO_TIMED_GRANT_OUTSIDE_WORKSPACE', None)
            self.assertFalse(store.timed_grant_allowed_outside_workspace())

    def test_the_usual_truthy_spellings_turn_it_on(self) -> None:
        for value in ('1', 'true', 'TRUE', 'yes', 'on', ' On '):
            with self.subTest(value=value), patch.dict(
                os.environ, {'KATO_TIMED_GRANT_OUTSIDE_WORKSPACE': value},
            ):
                self.assertTrue(store.timed_grant_allowed_outside_workspace())

    def test_anything_else_is_off(self) -> None:
        # Including the empty string a cleared settings.json field exports —
        # a blank must never read as "enabled" for a security switch.
        for value in ('', '0', 'false', 'no', 'off', 'maybe'):
            with self.subTest(value=value), patch.dict(
                os.environ, {'KATO_TIMED_GRANT_OUTSIDE_WORKSPACE': value},
            ):
                self.assertFalse(store.timed_grant_allowed_outside_workspace())

    def test_it_is_read_live_not_cached_at_import(self) -> None:
        # The Settings UI writes the value and the operator expects the NEXT
        # prompt to honour it, not the next restart.
        with patch.dict(os.environ, {'KATO_TIMED_GRANT_OUTSIDE_WORKSPACE': 'on'}):
            self.assertTrue(store.timed_grant_allowed_outside_workspace())
        with patch.dict(os.environ, {'KATO_TIMED_GRANT_OUTSIDE_WORKSPACE': 'off'}):
            self.assertFalse(store.timed_grant_allowed_outside_workspace())

    def test_the_switch_does_not_widen_ELIGIBILITY(self) -> None:
        # It governs WHERE a timed grant may apply, never WHICH commands can
        # receive one. ``rm -rf`` is not eligible at any setting.
        with patch.dict(os.environ, {'KATO_TIMED_GRANT_OUTSIDE_WORKSPACE': 'on'}):
            self.assertFalse(store.timed_grant_eligible('Bash', ['rm']))
            self.assertTrue(store.timed_grant_eligible('Bash', ['docker']))


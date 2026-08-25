"""A chat spawns the CLI its TAB is on, not the configured backend's.

Reported as ``error: unknown option '--json'`` on every Codex message — the
commander.js refusal the *claude* binary gives when handed ``codex exec
--json``. The session ROUTER picked the Codex manager correctly; the spawn
arguments came from the one PlanningSessionRunner, built at boot from the
configured backend's config block, so ``binary`` was always ``claude``.

Two halves have to agree: which manager runs the turn, and which CLI that
manager is told to execute. These pin the second.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from kato_core_lib.data_layers.service.planning_session_runner import (
    PlanningSessionRunner,
    StreamingSessionDefaults,
)


def _defaults(binary: str, model: str = '') -> StreamingSessionDefaults:
    return StreamingSessionDefaults(binary=binary, model=model)


class SpawnBinaryFollowsTheTabTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = MagicMock()
        self.backend = 'claude'
        self.manager.backend_for.side_effect = lambda task_id: self.backend
        self.runner = PlanningSessionRunner(
            session_manager=self.manager,
            defaults=_defaults('claude', model='opus'),
            defaults_by_backend={
                'claude': _defaults('claude', model='opus'),
                'codex': _defaults('codex', model='gpt-5-codex'),
            },
        )

    def _spawn(self):
        self.runner._start_session(
            task_id='T1', task_summary='s', initial_prompt='go', cwd='/w',
        )
        return self.manager.start_session.call_args.kwargs

    def test_a_claude_task_spawns_the_claude_binary(self) -> None:
        self.assertEqual(self._spawn()['binary'], 'claude')

    def test_a_codex_task_spawns_the_CODEX_binary(self) -> None:
        """The bug: this spawned ``claude`` and the turn died on --json."""
        self.backend = 'codex'
        self.assertEqual(self._spawn()['binary'], 'codex')

    def test_the_model_follows_the_backend_too(self) -> None:
        # A Claude model name is refused by the Codex API, so the whole
        # defaults set has to move together, not just the binary.
        self.backend = 'codex'
        self.assertEqual(self._spawn()['model'], 'gpt-5-codex')

    def test_an_unknown_backend_falls_back_to_the_configured_defaults(self) -> None:
        self.backend = 'openhands'
        self.assertEqual(self._spawn()['binary'], 'claude')

    def test_a_manager_that_cannot_answer_falls_back(self) -> None:
        # Single-backend host: no router, so no ``backend_for``.
        manager = MagicMock(spec=['start_session'])
        runner = PlanningSessionRunner(
            session_manager=manager,
            defaults=_defaults('claude'),
            defaults_by_backend={'codex': _defaults('codex')},
        )
        runner._start_session(
            task_id='T1', task_summary='s', initial_prompt='go', cwd='/w',
        )
        self.assertEqual(manager.start_session.call_args.kwargs['binary'], 'claude')

    def test_a_resolver_that_raises_falls_back(self) -> None:
        self.manager.backend_for.side_effect = RuntimeError('down')
        self.assertEqual(self._spawn()['binary'], 'claude')

    def test_no_map_at_all_keeps_the_old_behaviour(self) -> None:
        runner = PlanningSessionRunner(
            session_manager=self.manager, defaults=_defaults('claude'),
        )
        runner._start_session(
            task_id='T1', task_summary='s', initial_prompt='go', cwd='/w',
        )
        self.assertEqual(
            self.manager.start_session.call_args.kwargs['binary'], 'claude',
        )


class FromConfigBuildsEveryBackendTests(unittest.TestCase):
    """``from_config`` must collect BOTH blocks, not only the active one."""

    def test_both_backends_get_their_own_defaults(self) -> None:
        cfg = SimpleNamespace(
            claude=SimpleNamespace(binary='claude', model='opus'),
            codex=SimpleNamespace(binary='codex', model='gpt-5-codex'),
        )
        runner = PlanningSessionRunner.from_config(
            cfg, 'claude', MagicMock(),
        )
        self.assertEqual(runner._defaults_by_backend['claude'].binary, 'claude')
        self.assertEqual(runner._defaults_by_backend['codex'].binary, 'codex')

    def test_a_missing_codex_block_is_simply_absent(self) -> None:
        cfg = SimpleNamespace(claude=SimpleNamespace(binary='claude'), codex=None)
        runner = PlanningSessionRunner.from_config(cfg, 'claude', MagicMock())
        self.assertNotIn('codex', runner._defaults_by_backend)


if __name__ == '__main__':
    unittest.main()

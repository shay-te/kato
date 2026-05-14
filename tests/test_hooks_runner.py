"""Tests for the hook-firing runner. Uses an injected fake
subprocess so no real shells are spawned."""

from __future__ import annotations

import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from kato_core_lib.hooks.config import (
    HookConfig,
    HookDefinition,
    HookPoint,
)
from kato_core_lib.hooks.runner import HookResult, HookRunner


def _config(*hooks) -> HookConfig:
    by_point: dict[HookPoint, list[HookDefinition]] = {}
    for hook in hooks:
        by_point.setdefault(hook.point, []).append(hook)
    return HookConfig(
        hooks_by_point={p: tuple(hs) for p, hs in by_point.items()},
    )


def _fake_run(returncode: int = 0, stdout: str = '', stderr: str = ''):
    """Return a fake subprocess.run that captures its kwargs and
    returns a CompletedProcess-like result."""
    captured = {'calls': []}

    def run(argv, **kwargs):
        captured['calls'].append({'argv': argv, **kwargs})
        return SimpleNamespace(
            returncode=returncode, stdout=stdout, stderr=stderr,
        )

    run.captured = captured
    return run


class HookRunnerBasicTests(unittest.TestCase):

    def test_no_hooks_at_point_returns_empty_list(self) -> None:
        runner = HookRunner(_config())
        self.assertEqual(runner.fire(HookPoint.SESSION_END, {}), [])

    def test_fire_runs_each_matching_hook(self) -> None:
        hook1 = HookDefinition(
            point=HookPoint.SESSION_END, command='cmd1', match={},
        )
        hook2 = HookDefinition(
            point=HookPoint.SESSION_END, command='cmd2', match={},
        )
        run = _fake_run(returncode=0)
        runner = HookRunner(_config(hook1, hook2), subprocess_run=run)

        results = runner.fire(HookPoint.SESSION_END, {'task_id': 'T1'})

        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.ok for r in results))
        self.assertEqual(len(run.captured['calls']), 2)

    def test_only_matching_hooks_fire(self) -> None:
        matching = HookDefinition(
            point=HookPoint.PRE_TOOL_USE, command='audit',
            match={'tool': 'Bash'},
        )
        not_matching = HookDefinition(
            point=HookPoint.PRE_TOOL_USE, command='lint',
            match={'tool': 'Edit'},
        )
        run = _fake_run()
        runner = HookRunner(
            _config(matching, not_matching), subprocess_run=run,
        )

        results = runner.fire(HookPoint.PRE_TOOL_USE, {'tool': 'Bash'})

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].command, 'audit')

    def test_event_is_piped_as_json_stdin(self) -> None:
        hook = HookDefinition(
            point=HookPoint.SESSION_END, command='cat', match={},
        )
        run = _fake_run()
        runner = HookRunner(_config(hook), subprocess_run=run)
        runner.fire(HookPoint.SESSION_END, {'task_id': 'PROJ-1', 'count': 3})

        call = run.captured['calls'][0]
        # input= contains JSON of the event.
        import json
        parsed = json.loads(call['input'])
        self.assertEqual(parsed['task_id'], 'PROJ-1')
        self.assertEqual(parsed['count'], 3)


class HookRunnerSubstitutionTests(unittest.TestCase):

    def test_placeholder_substituted_in_command(self) -> None:
        hook = HookDefinition(
            point=HookPoint.POST_TOOL_USE,
            command='echo ${task_id} ${file_path}',
            match={},
        )
        run = _fake_run()
        runner = HookRunner(_config(hook), subprocess_run=run)
        runner.fire(HookPoint.POST_TOOL_USE, {
            'task_id': 'PROJ-1', 'file_path': '/tmp/x.py',
        })

        call = run.captured['calls'][0]
        rendered = call['argv'][-1]  # /bin/sh -c <rendered>
        self.assertEqual(rendered, 'echo PROJ-1 /tmp/x.py')

    def test_missing_key_substitutes_to_empty_string(self) -> None:
        # Defensive: a hook that references ``${file_path}`` for
        # a session_start event (no file_path field) must not crash.
        hook = HookDefinition(
            point=HookPoint.SESSION_START,
            command='echo file=${file_path}',
            match={},
        )
        run = _fake_run()
        runner = HookRunner(_config(hook), subprocess_run=run)
        runner.fire(HookPoint.SESSION_START, {'task_id': 'T1'})

        rendered = run.captured['calls'][0]['argv'][-1]
        self.assertEqual(rendered, 'echo file=')

    def test_unknown_placeholder_syntax_left_intact(self) -> None:
        # Only ``${name}`` is the placeholder format. ``$name`` or
        # ``%(name)s`` etc. pass through unchanged.
        hook = HookDefinition(
            point=HookPoint.SESSION_END,
            command='echo $TASK_ID %(task)s',
            match={},
        )
        run = _fake_run()
        runner = HookRunner(_config(hook), subprocess_run=run)
        runner.fire(HookPoint.SESSION_END, {'task_id': 'T1'})

        rendered = run.captured['calls'][0]['argv'][-1]
        self.assertEqual(rendered, 'echo $TASK_ID %(task)s')


class HookRunnerPreToolUseBlockingTests(unittest.TestCase):

    def test_pre_tool_use_zero_exit_does_NOT_block(self) -> None:
        hook = HookDefinition(
            point=HookPoint.PRE_TOOL_USE, command='echo ok', match={},
        )
        run = _fake_run(returncode=0)
        runner = HookRunner(_config(hook), subprocess_run=run)

        results = runner.fire(HookPoint.PRE_TOOL_USE, {'tool': 'Bash'})
        self.assertTrue(results[0].ok)
        self.assertFalse(results[0].blocked)
        self.assertFalse(runner.is_blocked(results))

    def test_pre_tool_use_non_zero_exit_BLOCKS(self) -> None:
        # The contract that makes hooks operator-useful: a
        # pre_tool_use hook returning exit 1 denies the tool call.
        hook = HookDefinition(
            point=HookPoint.PRE_TOOL_USE, command='exit 1', match={},
        )
        run = _fake_run(returncode=1, stderr='blocked dangerous rm')
        runner = HookRunner(_config(hook), subprocess_run=run)

        results = runner.fire(HookPoint.PRE_TOOL_USE, {'tool': 'Bash'})
        self.assertFalse(results[0].ok)
        self.assertTrue(results[0].blocked)
        self.assertTrue(runner.is_blocked(results))
        self.assertEqual(results[0].stderr, 'blocked dangerous rm')

    def test_post_tool_use_non_zero_exit_does_NOT_block(self) -> None:
        # Only pre_tool_use blocks. Post hooks are advisory —
        # they ran AFTER the tool, blocking is meaningless.
        hook = HookDefinition(
            point=HookPoint.POST_TOOL_USE, command='exit 1', match={},
        )
        run = _fake_run(returncode=1)
        runner = HookRunner(_config(hook), subprocess_run=run)

        results = runner.fire(HookPoint.POST_TOOL_USE, {'tool': 'Bash'})
        self.assertFalse(results[0].ok)
        self.assertFalse(results[0].blocked)
        self.assertFalse(runner.is_blocked(results))

    def test_session_end_non_zero_exit_does_NOT_block(self) -> None:
        hook = HookDefinition(
            point=HookPoint.SESSION_END, command='exit 1', match={},
        )
        run = _fake_run(returncode=1)
        runner = HookRunner(_config(hook), subprocess_run=run)
        results = runner.fire(HookPoint.SESSION_END, {})
        self.assertFalse(results[0].blocked)


class HookRunnerFailureRecoveryTests(unittest.TestCase):

    def test_timeout_returns_error_result_does_not_crash(self) -> None:
        hook = HookDefinition(
            point=HookPoint.SESSION_END, command='sleep 999',
            match={}, timeout_seconds=0.1,
        )

        def timing_out(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd='sh', timeout=0.1)

        runner = HookRunner(_config(hook), subprocess_run=timing_out)
        results = runner.fire(HookPoint.SESSION_END, {})

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].ok)
        self.assertIn('timed out', results[0].error)

    def test_spawn_failure_returns_error_result(self) -> None:
        hook = HookDefinition(
            point=HookPoint.POST_TOOL_USE, command='/nonexistent/binary',
            match={},
        )

        def os_error(*args, **kwargs):
            raise OSError('command not found')

        runner = HookRunner(_config(hook), subprocess_run=os_error)
        results = runner.fire(HookPoint.POST_TOOL_USE, {})

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].ok)
        self.assertIn('spawn failed', results[0].error)

    def test_pre_tool_use_spawn_failure_BLOCKS_fail_safe(self) -> None:
        # Important security property: if a pre_tool_use hook
        # can't even spawn, we don't know what its decision
        # would be. Fail-safe: BLOCK the tool. Operator notices
        # the misconfigured hook instead of silently bypassing
        # the guard they set up.
        hook = HookDefinition(
            point=HookPoint.PRE_TOOL_USE, command='/nonexistent/blocker',
            match={},
        )

        def os_error(*args, **kwargs):
            raise OSError('binary missing')

        runner = HookRunner(_config(hook), subprocess_run=os_error)
        results = runner.fire(HookPoint.PRE_TOOL_USE, {'tool': 'Bash'})

        self.assertTrue(results[0].blocked)
        self.assertTrue(runner.is_blocked(results))

    def test_pre_tool_use_timeout_BLOCKS_fail_safe(self) -> None:
        # Same fail-safe contract: timeout on pre_tool_use → BLOCK.
        hook = HookDefinition(
            point=HookPoint.PRE_TOOL_USE, command='sleep 999',
            match={}, timeout_seconds=0.05,
        )

        def timing_out(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd='sh', timeout=0.05)

        runner = HookRunner(_config(hook), subprocess_run=timing_out)
        results = runner.fire(HookPoint.PRE_TOOL_USE, {'tool': 'Bash'})

        self.assertTrue(results[0].blocked)


if __name__ == '__main__':
    unittest.main()

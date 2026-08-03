"""The shared CLI-transport mixin's abstract hooks must fail LOUDLY.

The mixin's whole design argument is that the spawn-side hooks are abstract and
never defaulted: this library may not import the sandbox library, so a
defaulted ``_run_prompt_result`` would make containment FAIL OPEN — a transport
that forgot to override it would silently run the agent unsandboxed, with
nothing at the call site to reveal it.

That argument was untested. These tests pin it: every hook raises
``NotImplementedError`` naming itself, so a half-built transport dies on first
use instead of quietly doing the wrong thing.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_core_lib.agent_core_lib.cli_agent_shared import CliAgentSharedBehaviour

# Every hook the mixin declares abstract, with a call that reaches it.
ABSTRACT_HOOKS = {
    '_run_prompt_result': lambda c: c._run_prompt_result(prompt='p', cwd='/w'),
    '_build_implementation_prompt': lambda c: c._build_implementation_prompt(object()),
    '_build_testing_prompt': lambda c: c._build_testing_prompt(object()),
    '_run_model_access_validation': lambda c: c._run_model_access_validation(),
    'fix_review_comments': lambda c: c.fix_review_comments([], 'branch'),
}


class _BareTransport(CliAgentSharedBehaviour):
    """A transport that inherits the mixin and overrides nothing."""

    def __init__(self) -> None:
        self._binary = 'agent'
        self._binary_path = ''
        self._repository_root_path = '/repos'
        self._model_smoke_test_enabled = True
        self._model_access_smoke_test_ran = False
        self.logger = SimpleNamespace(info=lambda *a, **k: None,
                                      warning=lambda *a, **k: None)


class AbstractHookTests(unittest.TestCase):
    def test_every_hook_raises_rather_than_defaulting(self) -> None:
        client = _BareTransport()
        for name, call in ABSTRACT_HOOKS.items():
            with self.subTest(hook=name):
                with self.assertRaises(NotImplementedError) as ctx:
                    call(client)
                self.assertIn(name, str(ctx.exception),
                              'the error must name the hook the transport forgot')

    def test_the_spawn_hook_is_reached_through_implement_task(self) -> None:
        # The containment-critical path: a transport with no _run_prompt_result
        # must not get a silent no-op spawn.
        client = _BareTransport()
        client._build_implementation_prompt = lambda *a, **k: 'prompt'
        with self.assertRaises(NotImplementedError):
            client.implement_task(SimpleNamespace(id='T1'))

    def test_the_spawn_hook_is_reached_through_test_task(self) -> None:
        client = _BareTransport()
        client._build_testing_prompt = lambda *a, **k: 'prompt'
        with self.assertRaises(NotImplementedError):
            client.test_task(SimpleNamespace(id='T1'))

    def test_fix_review_comment_delegates_to_the_batch_hook(self) -> None:
        client = _BareTransport()
        with self.assertRaises(NotImplementedError) as ctx:
            client.fix_review_comment(SimpleNamespace(), 'branch')
        self.assertIn('fix_review_comments', str(ctx.exception))


class SmokeTestGateTests(unittest.TestCase):
    """``validate_model_access`` runs the per-CLI probe AT MOST once."""

    def _client(self):
        client = _BareTransport()
        client.calls = 0

        def probe():
            client.calls += 1
        client._run_model_access_validation = probe
        return client

    def test_runs_once_and_then_never_again(self) -> None:
        client = self._client()
        client.validate_model_access()
        client.validate_model_access()
        client.validate_model_access()
        self.assertEqual(client.calls, 1)

    def test_the_opt_in_wrapper_is_a_no_op_when_disabled(self) -> None:
        client = self._client()
        client._model_smoke_test_enabled = False
        client._validate_model_smoke_test()
        self.assertEqual(client.calls, 0)

    def test_the_opt_in_wrapper_runs_the_probe_when_enabled(self) -> None:
        client = self._client()
        client._validate_model_smoke_test()
        self.assertEqual(client.calls, 1)


class SharedPureLogicTests(unittest.TestCase):
    def test_coerce_max_turns_accepts_a_positive_cap(self) -> None:
        coerce = CliAgentSharedBehaviour._coerce_max_turns
        self.assertEqual(coerce(5), 5)
        self.assertEqual(coerce('7'), 7)

    def test_coerce_max_turns_returns_none_for_no_cap(self) -> None:
        coerce = CliAgentSharedBehaviour._coerce_max_turns
        for no_cap in (None, '', 0, -1, 'abc', [], {}):
            self.assertIsNone(coerce(no_cap), repr(no_cap))

    def test_coerce_max_turns_truncates_a_float(self) -> None:
        # int(1.9) is 1, not a rejection. Stated explicitly because the
        # surrounding cases all fall back to "no cap" and a reader could
        # reasonably expect a non-integer to do the same.
        self.assertEqual(CliAgentSharedBehaviour._coerce_max_turns(1.9), 1)
        self.assertIsNone(CliAgentSharedBehaviour._coerce_max_turns(0.5))

    def test_host_binary_prefers_the_resolved_path(self) -> None:
        client = _BareTransport()
        self.assertEqual(client._host_binary(), 'agent')
        client._binary_path = '/usr/local/bin/agent'
        self.assertEqual(client._host_binary(), '/usr/local/bin/agent')

    def test_review_comment_cwd_prefers_the_comments_own_clone(self) -> None:
        client = _BareTransport()
        comment = SimpleNamespace(repository_local_path='/repos/app')
        self.assertEqual(client._review_comment_cwd(comment), '/repos/app')

    def test_review_comment_cwd_falls_back_to_the_repository_root(self) -> None:
        client = _BareTransport()
        self.assertEqual(client._review_comment_cwd(SimpleNamespace()), '/repos')

    def test_review_comment_cwd_falls_back_to_the_process_cwd(self) -> None:
        client = _BareTransport()
        client._repository_root_path = ''
        with patch('agent_core_lib.agent_core_lib.cli_agent_shared.os.getcwd',
                   return_value='/here'):
            self.assertEqual(client._review_comment_cwd(SimpleNamespace()), '/here')

    def test_working_directories_leads_with_the_first_repo(self) -> None:
        client = _BareTransport()
        prepared = SimpleNamespace(repositories=[
            SimpleNamespace(local_path='/repos/a'),
            SimpleNamespace(local_path='/repos/b'),
        ])
        self.assertEqual(client._working_directories(prepared), ('/repos/a', ['/repos/b']))

    def test_working_directories_deduplicates_repeated_paths(self) -> None:
        # A duplicate would widen the sandbox scope with a redundant --add-dir.
        client = _BareTransport()
        prepared = SimpleNamespace(repositories=[
            SimpleNamespace(local_path='/repos/a'),
            SimpleNamespace(local_path='/repos/a'),
            SimpleNamespace(local_path='/repos/b'),
        ])
        self.assertEqual(client._working_directories(prepared), ('/repos/a', ['/repos/b']))

    def test_working_directories_ignores_repos_with_no_local_path(self) -> None:
        client = _BareTransport()
        prepared = SimpleNamespace(repositories=[
            SimpleNamespace(local_path=''),
            SimpleNamespace(local_path=None),
            SimpleNamespace(local_path='/repos/a'),
        ])
        self.assertEqual(client._working_directories(prepared), ('/repos/a', []))

    def test_working_directories_with_no_task_uses_the_repository_root(self) -> None:
        client = _BareTransport()
        self.assertEqual(client._working_directories(None), ('/repos', []))
        self.assertEqual(
            client._working_directories(SimpleNamespace(repositories=[])), ('/repos', []))

    def test_host_binary_argv_consults_the_windows_shim_bypass(self) -> None:
        client = _BareTransport()
        bypass = ('agent_core_lib.agent_core_lib.cli_agent_shared'
                  '.resolve_windows_cli_invocation')
        with patch(bypass, return_value=['node.exe', 'cli.js']):
            self.assertEqual(client._host_binary_argv(), ['node.exe', 'cli.js'])
        with patch(bypass, return_value=None):
            self.assertEqual(client._host_binary_argv(), ['agent'])


if __name__ == '__main__':
    unittest.main()

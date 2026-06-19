"""End-to-end flow tests for ``GitClientMixin`` — A-Z scenarios.

Each test class represents one named flow and exercises the full call chain
from a public/host method down through the mocked ``subprocess.run`` layer
back to the structured result.  Nothing inside the mixin is patched; only the
lowest-level ``subprocess.run`` is intercepted so the real command assembly,
output parsing, retry, and rebase logic all run.

No network access and no real ``git`` invocation occur: every git command is
served by a scripted ``subprocess.run`` double, and the assertions check that
the right git arguments are issued, in the right order, and that the textual
output is parsed into the expected structures.
"""
from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from git_core_lib.git_core_lib.client.git_client import GitClientMixin

RUN_TARGET = 'git_core_lib.git_core_lib.client.git_client.subprocess.run'

REPO_PATH = '/work/example-repo'
FEATURE_BRANCH = 'feature/widget'
DEFAULT_BRANCH = 'main'


class _Client(GitClientMixin):
    """Minimal concrete host class — supplies the required ``logger``."""

    def __init__(self) -> None:
        self.logger = logging.getLogger('test.git_flow')


def _completed(returncode=0, stdout='', stderr=''):
    """A stand-in for ``subprocess.CompletedProcess``."""
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _git_args(call):
    """The git sub-arguments (everything after the ``-C <path>`` prefix) for a
    recorded ``subprocess.run`` call — strips the ``git``/``-c``/``-C`` framing
    so assertions read against the meaningful command, e.g. ``['status',
    '--porcelain']``."""
    argv = call.args[0]
    if '-C' not in argv:
        return argv
    return argv[argv.index('-C') + 2:]


class _GitDouble(object):
    """A scripted ``subprocess.run`` replacement.

    Routes each git invocation to a response keyed by its first meaningful
    sub-command (``fetch``, ``rebase``, ``push``, ``status``…).  Each entry is
    a list consumed in order, so a command can succeed, then fail, then
    succeed across the flow.  Records every call for in-order assertions.
    """

    def __init__(self, script: dict) -> None:
        self._script = {key: list(values) for key, values in script.items()}
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(SimpleNamespace(args=(argv,), kwargs=kwargs))
        sub_args = _git_args(SimpleNamespace(args=(argv,)))
        key = sub_args[0] if sub_args else ''
        queue = self._script.get(key)
        if not queue:
            return _completed(returncode=0, stdout='')
        result = queue.pop(0)
        return result

    def issued(self):
        """Ordered list of the first sub-command of every git call."""
        return [
            (_git_args(call)[0] if _git_args(call) else '')
            for call in self.calls
        ]


# ---------------------------------------------------------------------------
# F1 — Happy path A-Z: discover default branch → sync (fetch + rebase) →
#      inspect working tree + grep → push the feature branch.
# ---------------------------------------------------------------------------
class F1_DiscoverSyncInspectPush(unittest.TestCase):
    """The whole lifecycle a caller drives against one repo, start to finish."""

    def test_flow(self) -> None:
        client = _Client()

        script = {
            # A — default-branch discovery (symbolic-ref answers first probe).
            'symbolic-ref': [
                _completed(stdout=f'refs/remotes/origin/{DEFAULT_BRANCH}\n'),
            ],
            # C — fetch the remote feature branch before rebasing.
            'fetch': [_completed(stdout='')],
            # D — the just-fetched remote ref exists.
            'rev-parse': [_completed(returncode=0, stdout='')],
            # E — rebase onto origin/<branch> succeeds.
            'rebase': [_completed(stdout='')],
            # F — working tree has one modified + one new file.
            'status': [
                _completed(stdout=' M src/widget.py\n?? src/new_widget.py\n'),
            ],
            # G — grep finds a symbol across two files.
            'grep': [
                _completed(
                    returncode=0,
                    stdout=(
                        'src/widget.py:10:def build_widget(self):\n'
                        'src/new_widget.py:2:# build_widget helper\n'
                    ),
                ),
            ],
            # H — push the branch upstream.
            'push': [_completed(stdout='')],
        }
        double = _GitDouble(script)

        with patch.object(GitClientMixin, '_validate_git_executable'), \
             patch(RUN_TARGET, side_effect=double):
            # A — discover the repository's default branch.
            default_branch = GitClientMixin._infer_default_branch(REPO_PATH)
            self.assertEqual(default_branch, DEFAULT_BRANCH)

            # B-E — bring the feature branch in line with origin (fetch+rebase).
            client._sync_branch_with_remote(REPO_PATH, FEATURE_BRANCH)

            # F — read the porcelain working-tree status.
            status_output = client._working_tree_status(REPO_PATH)
            self.assertIn('src/widget.py', status_output)
            self.assertIn('src/new_widget.py', status_output)

            # G — content search across tracked + untracked files.
            matches = client.git_grep(REPO_PATH, 'build_widget')

            # H — publish the branch to origin.
            client._push_branch(REPO_PATH, FEATURE_BRANCH)

        # Parsing assertions — grep output became structured triples.
        self.assertEqual(matches, [
            {'path': 'src/widget.py', 'line': 10, 'text': 'def build_widget(self):'},
            {'path': 'src/new_widget.py', 'line': 2, 'text': '# build_widget helper'},
        ])

        # Ordering assertion — the lifecycle issued git commands in sequence.
        issued = double.issued()
        self.assertEqual(
            issued,
            [
                'symbolic-ref',  # A
                'fetch',         # C
                'rev-parse',     # D
                'rebase',        # E
                'status',        # F
                'grep',          # G
                'push',          # H
            ],
        )

        # The fetch maps the remote branch onto a local remote-tracking ref.
        fetch_call = next(c for c in double.calls if _git_args(c)[0] == 'fetch')
        self.assertEqual(
            _git_args(fetch_call),
            [
                'fetch',
                'origin',
                f'{FEATURE_BRANCH}:refs/remotes/origin/{FEATURE_BRANCH}',
            ],
        )

        # The rebase targets the remote-tracking branch.
        rebase_call = next(c for c in double.calls if _git_args(c)[0] == 'rebase')
        self.assertEqual(_git_args(rebase_call), ['rebase', f'origin/{FEATURE_BRANCH}'])

        # The push sets upstream on origin for the feature branch.
        push_call = next(c for c in double.calls if _git_args(c)[0] == 'push')
        self.assertEqual(
            _git_args(push_call),
            ['push', '-u', 'origin', FEATURE_BRANCH],
        )

    def test_every_git_call_disables_hooks_and_suppresses_prompt(self) -> None:
        """Cross-cutting safety: every git invocation disables hooks
        (``core.hooksPath=/dev/null`` is baked into the command), and every
        credentialed invocation suppresses the terminal prompt
        (``GIT_TERMINAL_PROMPT=0`` is set on its env)."""
        client = _Client()
        double = _GitDouble({
            'fetch': [_completed(stdout='')],
            'rev-parse': [_completed(returncode=0)],
            'rebase': [_completed(stdout='')],
            'push': [_completed(stdout='')],
        })

        with patch.object(GitClientMixin, '_validate_git_executable'), \
             patch(RUN_TARGET, side_effect=double):
            client._sync_branch_with_remote(REPO_PATH, FEATURE_BRANCH)
            client._push_branch(REPO_PATH, FEATURE_BRANCH)

        self.assertTrue(double.calls)
        prompt_suppressed_count = 0
        for recorded in double.calls:
            argv = recorded.args[0]
            # Hook disabling is part of every assembled git command.
            self.assertIn('-c', argv)
            self.assertIn('core.hooksPath=/dev/null', argv)
            # The credentialed subprocess path sets a hardened env.
            env = recorded.kwargs.get('env')
            if env is not None:
                self.assertEqual(env.get('GIT_TERMINAL_PROMPT'), '0')
                prompt_suppressed_count += 1
        # The mutating commands (fetch/rebase/push) went through the
        # credentialed path with prompt suppression.
        self.assertGreaterEqual(prompt_suppressed_count, 3)


# ---------------------------------------------------------------------------
# F2 — Push rejected (non-fast-forward) → auto fetch + rebase → push again.
#      The key recovery transition in the publish flow.
# ---------------------------------------------------------------------------
class F2_PushRejectedThenRebaseAndRetry(unittest.TestCase):
    """origin moved ahead: first push is rejected, the mixin syncs, retries,
    and the second push lands."""

    def test_flow(self) -> None:
        client = _Client()

        script = {
            # First push is rejected as non-fast-forward; second succeeds.
            'push': [
                _completed(
                    returncode=1,
                    stderr=(
                        '! [rejected] feature/widget -> feature/widget '
                        '(non-fast-forward)\nUpdates were rejected because the '
                        'remote contains work that you do not have locally.'
                    ),
                ),
                _completed(stdout=''),
            ],
            'fetch': [_completed(stdout='')],
            'rev-parse': [_completed(returncode=0, stdout='')],
            'rebase': [_completed(stdout='')],
        }
        double = _GitDouble(script)

        with patch.object(GitClientMixin, '_validate_git_executable'), \
             patch(RUN_TARGET, side_effect=double):
            client._push_branch(REPO_PATH, FEATURE_BRANCH)

        # The recovery sequence: push(reject) → fetch → rev-parse → rebase → push(ok).
        self.assertEqual(
            double.issued(),
            ['push', 'fetch', 'rev-parse', 'rebase', 'push'],
        )

    def test_unrelated_push_failure_is_not_retried(self) -> None:
        """A push failure that is NOT a non-fast-forward rejection (e.g. auth)
        surfaces immediately — no fetch/rebase recovery is attempted."""
        client = _Client()
        double = _GitDouble({
            'push': [
                _completed(returncode=1, stderr='fatal: Authentication failed'),
            ],
        })

        with patch.object(GitClientMixin, '_validate_git_executable'), \
             patch(RUN_TARGET, side_effect=double):
            with self.assertRaisesRegex(RuntimeError, 'failed to push branch'):
                client._push_branch(REPO_PATH, FEATURE_BRANCH)

        # Only the single push was attempted; no recovery commands ran.
        self.assertEqual(double.issued(), ['push'])


# ---------------------------------------------------------------------------
# F3 — Rebase conflicts during sync → abort the rebase and re-raise.
#      The error transition guarding the sync step.
# ---------------------------------------------------------------------------
class F3_RebaseConflictAbortsAndRaises(unittest.TestCase):
    """If the rebase hits a conflict, the mixin aborts it (leaving a clean
    tree) and propagates the failure rather than continuing."""

    def test_flow(self) -> None:
        client = _Client()

        script = {
            'fetch': [_completed(stdout='')],
            'rev-parse': [_completed(returncode=0, stdout='')],
            # Rebase fails (conflict); abort then succeeds.
            'rebase': [
                _completed(returncode=1, stderr='CONFLICT (content): merge conflict'),
                _completed(stdout=''),  # rebase --abort
            ],
        }
        double = _GitDouble(script)

        with patch.object(GitClientMixin, '_validate_git_executable'), \
             patch(RUN_TARGET, side_effect=double):
            with self.assertRaisesRegex(RuntimeError, 'failed to rebase branch'):
                client._sync_branch_with_remote(REPO_PATH, FEATURE_BRANCH)

        # fetch → rev-parse → rebase(conflict) → rebase --abort.
        self.assertEqual(
            double.issued(),
            ['fetch', 'rev-parse', 'rebase', 'rebase'],
        )
        abort_calls = [c for c in double.calls if _git_args(c) == ['rebase', '--abort']]
        self.assertEqual(len(abort_calls), 1)


if __name__ == '__main__':
    unittest.main()

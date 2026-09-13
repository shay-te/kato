"""Tests for ``detect_default_branch``.

This helper is the *fallback* resolver — the diff endpoint
prefers the kato config's ``destination_branch`` and only consults
this helper when the inventory has nothing to say. We deliberately
removed the prior ``main``/``master``/``develop`` probe because
guessing the wrong base produced a wrong diff (a repo with default
``master`` but a configured base of ``develop`` was diffing
against the wrong ref).

Resolution chain (both ask the actual remote, neither guesses):

1. ``git symbolic-ref refs/remotes/origin/HEAD`` — local clone hint.
2. ``git ls-remote --symref origin HEAD`` — direct remote query.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from kato_webserver import git_diff_utils


class _GitStub:
    """Replay queue for ``run_git`` calls. Each call dequeues one
    entry; ``None`` means "git command failed" (the convention
    ``run_git`` already uses)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, cwd, args, *, timeout):
        self.calls.append(tuple(args))
        if not self._responses:
            return None
        return self._responses.pop(0)


def _patch_run_git(stub):
    return patch.object(git_diff_utils, 'run_git', side_effect=stub)


class DetectDefaultBranchTests(unittest.TestCase):
    def setUp(self) -> None:
        # ``detect_default_branch`` memoizes per clone path — resolving it
        # can cost a ``git ls-remote`` NETWORK round-trip, and the Files
        # tree asks once per repo on every load. Module-level state leaks
        # between cases, so each one starts from a cold cache.
        git_diff_utils.forget_default_branches()
        self.addCleanup(git_diff_utils.forget_default_branches)

    def test_returns_branch_from_local_origin_head_symref(self) -> None:
        # Fast path: the local clone has ``refs/remotes/origin/HEAD``
        # set, so we never need to talk to the remote.
        stub = _GitStub(['origin/main\n'])
        with _patch_run_git(stub):
            self.assertEqual(git_diff_utils.detect_default_branch('/repo'), 'main')
        self.assertEqual(len(stub.calls), 1)
        self.assertEqual(
            stub.calls[0],
            ('symbolic-ref', '--short', 'refs/remotes/origin/HEAD'),
        )

    def test_handles_bare_branch_name_in_symref_output(self) -> None:
        # Some git versions emit just ``develop`` rather than
        # ``origin/develop`` when --short is set. Pinning behaviour
        # so neither form regresses.
        stub = _GitStub(['develop\n'])
        with _patch_run_git(stub):
            self.assertEqual(
                git_diff_utils.detect_default_branch('/repo'),
                'develop',
            )

    def test_falls_back_to_ls_remote_when_local_head_is_unset(self) -> None:
        # Workspace clones sometimes ship without ``origin/HEAD``
        # set, which broke the old (symbolic-ref-only) detector.
        # ``ls-remote --symref`` asks the remote what HEAD points to,
        # works without the local ref state.
        ls_remote_output = 'ref: refs/heads/develop\tHEAD\n<sha>\tHEAD\n'
        stub = _GitStub([
            None,                # symbolic-ref → not set
            ls_remote_output,    # ls-remote --symref origin HEAD → develop
        ])
        with _patch_run_git(stub):
            self.assertEqual(
                git_diff_utils.detect_default_branch('/repo'),
                'develop',
            )
        self.assertEqual(
            stub.calls[1],
            ('ls-remote', '--symref', 'origin', 'HEAD'),
        )

    def test_does_not_guess_main_or_master_when_nothing_resolves(self) -> None:
        # Regression guard: previously this helper probed
        # ``origin/main`` then ``origin/master`` as a last-ditch
        # fallback. That returned the *remote's* default rather
        # than the configured task base — a source of wrong diffs.
        # The right answer when nothing resolves is empty string;
        # the caller surfaces an actionable error.
        stub = _GitStub([
            None,  # symbolic-ref fails
            None,  # ls-remote fails
        ])
        with _patch_run_git(stub):
            self.assertEqual(git_diff_utils.detect_default_branch('/repo'), '')
        # Crucially: only the two truthful probes ran. No
        # ``rev-parse origin/main`` or similar guessing.
        self.assertEqual(len(stub.calls), 2)

    def test_ls_remote_strips_refs_heads_prefix(self) -> None:
        # Defensive: branch names without the standard prefix come
        # through trimmed rather than mangled.
        ls_remote_output = 'ref: trunk\tHEAD\n'
        stub = _GitStub([None, ls_remote_output])
        with _patch_run_git(stub):
            self.assertEqual(
                git_diff_utils.detect_default_branch('/repo'),
                'trunk',
            )


if __name__ == '__main__':
    unittest.main()


class DefaultBranchCacheTests(unittest.TestCase):
    """Resolving the default branch is expensive; do it once per clone.

    When a clone has no ``origin/HEAD`` — a ``--reference`` clone often does
    not — the fallback is ``git ls-remote``, a NETWORK round-trip to the
    provider. The Files tree asks once per repo, so a 25-repo task paid 25 SSH
    handshakes every time the pane opened: "on reload the page he reload all
    the repos and it taking forever".
    """

    def setUp(self) -> None:
        git_diff_utils.forget_default_branches()
        self.addCleanup(git_diff_utils.forget_default_branches)

    def test_the_remote_is_asked_once_not_once_per_load(self) -> None:
        calls = []

        def fake_run_git(cwd, args, **kwargs):
            calls.append(args[0])
            return '' if args[0] == 'symbolic-ref' else 'ref: refs/heads/develop\tHEAD'

        with patch.object(git_diff_utils, 'run_git', side_effect=fake_run_git):
            first = git_diff_utils.detect_default_branch('/ws/repo')
            second = git_diff_utils.detect_default_branch('/ws/repo')
            third = git_diff_utils.detect_default_branch('/ws/repo')

        self.assertEqual([first, second, third], ['develop'] * 3)
        self.assertEqual(calls.count('ls-remote'), 1, f'asked the remote {calls.count("ls-remote")}x')

    def test_each_clone_is_cached_separately(self) -> None:
        answers = {'/ws/a': 'main', '/ws/b': 'develop'}

        def fake_run_git(cwd, args, **kwargs):
            if args[0] == 'symbolic-ref':
                return f'origin/{answers[cwd]}'
            return ''

        with patch.object(git_diff_utils, 'run_git', side_effect=fake_run_git):
            self.assertEqual(git_diff_utils.detect_default_branch('/ws/a'), 'main')
            self.assertEqual(git_diff_utils.detect_default_branch('/ws/b'), 'develop')
            # ...and still correct on the cached second pass.
            self.assertEqual(git_diff_utils.detect_default_branch('/ws/a'), 'main')

    def test_a_failure_is_not_cached_forever(self) -> None:
        """Caching '' permanently would pin a transient network blip.

        But not caching it at all means an unreachable remote costs a full
        timeout on every load, which is the problem in the first place — so
        the miss is remembered briefly and then retried.
        """
        # The clock is pinned on BOTH sides — the miss is recorded against it
        # too, so a fake "later" that is earlier than the real monotonic clock
        # would silently read as "still within the TTL".
        with patch.object(git_diff_utils.time, 'monotonic', return_value=100.0), \
                patch.object(git_diff_utils, 'run_git', return_value=''):
            self.assertEqual(git_diff_utils.detect_default_branch('/ws/down'), '')

        # Within the TTL: the remote is not asked again.
        with patch.object(git_diff_utils.time, 'monotonic', return_value=102.0), \
                patch.object(git_diff_utils, 'run_git') as never:
            self.assertEqual(git_diff_utils.detect_default_branch('/ws/down'), '')
            never.assert_not_called()

        # Past it, kato retries and a recovered remote is picked up.
        with patch.object(git_diff_utils.time, 'monotonic', return_value=200.0), \
                patch.object(git_diff_utils, 'run_git', return_value='origin/main'):
            self.assertEqual(git_diff_utils.detect_default_branch('/ws/down'), 'main')

    def test_a_miss_clears_within_seconds_not_minutes(self) -> None:
        """An unresolved base fails the Changes tab, the commit list AND diff
        context expansion closed. Remembering that for a minute after one blip
        — or right after a clone, before ``origin/HEAD`` exists — hides a repo
        that would have answered on the very next try. The window only has to
        be long enough to collapse one tree walk's burst.
        """
        self.assertLessEqual(
            git_diff_utils._DEFAULT_BRANCH_MISS_TTL_SECONDS, 10.0,
            'a cached miss disables the diff base while it lasts',
        )
        with patch.object(git_diff_utils.time, 'monotonic', return_value=100.0), \
                patch.object(git_diff_utils, 'run_git', return_value=''):
            self.assertEqual(git_diff_utils.detect_default_branch('/ws/fresh'), '')
        # Ten seconds later the repo has finished cloning and answers.
        with patch.object(git_diff_utils.time, 'monotonic', return_value=110.0), \
                patch.object(git_diff_utils, 'run_git', return_value='origin/main'):
            self.assertEqual(git_diff_utils.detect_default_branch('/ws/fresh'), 'main')

    def test_forget_clears_it_for_a_re_clone(self) -> None:
        # A repo that was deleted and cloned again can have a different
        # default branch; the reset is how that is picked up.
        with patch.object(git_diff_utils, 'run_git', return_value='origin/main'):
            self.assertEqual(git_diff_utils.detect_default_branch('/ws/r'), 'main')
        git_diff_utils.forget_default_branches()
        with patch.object(git_diff_utils, 'run_git', return_value='origin/develop'):
            self.assertEqual(git_diff_utils.detect_default_branch('/ws/r'), 'develop')


class SyncInvalidatesTheCacheTests(unittest.TestCase):
    """A sync can clone a repo that was missing, or replace a broken one.

    The cache is keyed by clone PATH and lives for the process, so a fresh
    clone landing on a different default branch would otherwise keep being
    diffed against the old one. ``forget_default_branches`` existed only as a
    test seam until this wired it where its docstring already claimed it ran.
    """

    def test_the_sync_route_drops_the_cached_branches(self) -> None:
        from unittest.mock import MagicMock
        from webserver.kato_webserver.app import create_app
        from webserver.kato_webserver import app as app_module

        git_diff_utils.forget_default_branches()
        self.addCleanup(git_diff_utils.forget_default_branches)

        with patch.object(git_diff_utils, 'run_git', return_value='origin/main'):
            self.assertEqual(git_diff_utils.detect_default_branch('/ws/r'), 'main')

        service = MagicMock()
        service.repositories.sync_task_repositories.return_value = {'synced': []}
        app = create_app(session_manager=MagicMock())
        app.config['AGENT_SERVICE'] = service
        with patch.object(app_module, 'forget_default_branches') as dropped:
            app.test_client().post('/api/sessions/T-1/sync-repositories')
        dropped.assert_called_once()


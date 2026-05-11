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

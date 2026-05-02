"""Doc-vs-behavior tests: validator actually behaves as the anchored invariants promise.

The drift guard in ``test_bypass_protections_doc_consistency.py`` enforces
**set-equality** between the anchored doc blocks and the code constants:
the lists of forbidden mount roots in
``BYPASS_PROTECTIONS.md`` must match the
``_FORBIDDEN_MOUNT_SOURCES_SUBTREE`` and
``_FORBIDDEN_MOUNT_SOURCES_EXACT`` constants in
``kato.sandbox.manager`` byte-for-byte (modulo path normalization).

That catches the most common drift — somebody adds, removes, or
renames an entry in one place and forgets the other. It does NOT
catch the case where the entries match but the validator's *behavior*
disagrees with the documented semantics. The clearest example is the
``~/.kato`` bug that motivated this whole batch: ``~/.kato`` was in
the SUBTREE block, the constant matched, the drift guard was happy —
but the documented semantic ("subtree-forbidden") was wrong (it
should have been exact-forbidden because legitimate per-task
workspaces live at ``~/.kato/workspaces/<task>/<repo>``). A test that
exercised "every subtree-forbidden path must reject a descendant" and
"every exact-forbidden path must accept a descendant where testable"
would have failed when the validator refused
``~/.kato/workspaces/foo/bar`` — flagging the doc-vs-behavior
disagreement, not just the code-vs-doc one.

This file adds those tests. The parser duplicates the small anchor
matcher from ``test_bypass_protections_doc_consistency.py`` — both
files share the format ``<!-- SECURITY-INVARIANTS:<group>:BEGIN -->
... :END -->`` with one bullet per line.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import unittest
from pathlib import Path

from kato.sandbox import manager


_DOC_PATH = Path(__file__).resolve().parent.parent / 'BYPASS_PROTECTIONS.md'

_ANCHOR_RE = re.compile(
    r'<!--\s*SECURITY-INVARIANTS:(?P<group>[a-z0-9-]+):BEGIN\s*-->\s*\n'
    r'(?P<body>.*?)'
    r'\n\s*<!--\s*SECURITY-INVARIANTS:\1:END\s*-->',
    re.DOTALL,
)
_ITEM_RE = re.compile(r'^\s*-\s+(.+?)\s*$', re.MULTILINE)


def _parse_anchor(group: str) -> set[str]:
    text = _DOC_PATH.read_text(encoding='utf-8')
    for match in _ANCHOR_RE.finditer(text):
        if match.group('group') == group:
            return {
                item.strip()
                for item in _ITEM_RE.findall(match.group('body'))
                if item.strip()
            }
    raise AssertionError(f'no anchor block named {group!r} in {_DOC_PATH}')


def _expand(doc_path_str: str) -> Path:
    """Expand a doc-form path string (``~/.ssh``, ``/etc``, ``~``) into a Path."""
    if doc_path_str == '~':
        return Path.home()
    if doc_path_str.startswith('~/'):
        return Path.home() / doc_path_str[2:]
    return Path(doc_path_str)


def _home_is_under_subtree_forbidden() -> Path | None:
    """Return a subtree-forbidden ancestor of ``Path.home()``, or None.

    Edge case for CI: a runner with ``$HOME=/root`` (running as root,
    which kato itself refuses for bypass mode but the validator can
    still be unit-tested in) would put ``Path.home()`` under the
    subtree-forbidden ``/root``. Descendants of ``~`` would then be
    correctly rejected by the validator — and the descendant-acceptance
    test below would fail not because the validator is broken, but
    because the test environment violates the test's own assumption.
    Skip cleanly in that case rather than confuse a future operator.
    """
    home = Path.home()
    from kato.sandbox import manager as _m
    for p in _m._FORBIDDEN_MOUNT_SOURCES_SUBTREE:
        try:
            if home == p or home.is_relative_to(p):
                return p
        except (ValueError, AttributeError):
            continue
    return None


# ----- exact-match descendants we can actually exercise -----
#
# For EXACT-forbidden paths the documented semantic is: "the path
# itself is refused, a descendant is allowed." Most exact-forbidden
# entries (``/``, ``/home``, ``/Users``) are not testable for the
# descendant-allowed direction in a portable way — every plausible
# descendant is itself either non-existent or under another forbidden
# subtree. The two we CAN exercise on a normal developer box:
#
#   * ``~``  — descendants are arbitrary subdirs of $HOME
#   * ``~/.kato`` — descendants are per-task workspace clones
#                   (the canonical legitimate use case)
#
# The other EXACT-forbidden entries are still asserted on the
# rejection direction (the path itself must raise SandboxError);
# they're just skipped on the accept-descendant direction.
_TESTABLE_EXACT_DESCENDANTS = frozenset({'~', '~/.kato'})


class _DocVsBehaviorTests(unittest.TestCase):
    """Every documented invariant must match the validator's actual behavior.

    Where ``test_sandbox_workspace_validation.py`` hard-codes specific
    paths, this file iterates the doc's anchored lists. The intent is
    to catch the case where someone adds (or renames, or moves between
    sets) an entry in both the doc anchor and the code constant — the
    drift guard would be happy, the standalone validator tests would
    be happy, but the validator's behavior would silently disagree
    with the documented semantic.
    """

    def setUp(self) -> None:
        self._cleanup: list[Path] = []

    def tearDown(self) -> None:
        for path in self._cleanup:
            shutil.rmtree(path, ignore_errors=True)

    # ----- subtree: every entry must reject self AND a descendant -----

    def test_every_subtree_forbidden_path_rejects_itself(self):
        """Each subtree-forbidden path must raise on its own. No exceptions."""
        for doc_path in sorted(_parse_anchor('forbidden-mount-subtree')):
            with self.subTest(path=doc_path):
                with self.assertRaises(
                    manager.SandboxError,
                    msg=f'{doc_path}: documented subtree-forbidden but validator accepted it',
                ):
                    manager._validate_workspace_path(str(_expand(doc_path)))

    def test_every_subtree_forbidden_path_rejects_a_descendant(self):
        """Each subtree-forbidden path must reject ``<path>/synthetic-child``
        with the SUBTREE error message specifically.

        This is the load-bearing direction: it's what makes "subtree"
        semantically different from "exact". We don't need to create
        the descendant on disk — the forbidden-check fires before the
        existence check in ``_validate_workspace_path``.

        Crucially: we assert the error message contains "is under
        sensitive directory" (the subtree-rejection wording in the
        validator). Otherwise a SandboxError raised for the wrong
        reason — e.g. "does not exist" because the synthetic
        descendant isn't on disk — would falsely pass this test.
        That distinction is what catches the doc-vs-behavior class
        of bug ``~/.kato`` originally exhibited.
        """
        for doc_path in sorted(_parse_anchor('forbidden-mount-subtree')):
            with self.subTest(path=doc_path):
                descendant = _expand(doc_path) / 'synthetic-child-for-test'
                try:
                    manager._validate_workspace_path(str(descendant))
                except manager.SandboxError as exc:
                    self.assertIn(
                        'under sensitive directory',
                        str(exc),
                        msg=(
                            f'{doc_path}: documented subtree-forbidden '
                            f'but validator rejected descendant '
                            f'{descendant} for the wrong reason '
                            f'({exc!s}). The validator is treating this '
                            f'entry as exact-only — either move it to '
                            f'the exact-match set or fix the validator.'
                        ),
                    )
                else:
                    self.fail(
                        f'{doc_path}: documented subtree-forbidden but '
                        f'validator ACCEPTED descendant {descendant}. '
                        'Either move this entry to the exact-match set '
                        'or fix the validator.'
                    )

    # ----- exact: every entry must reject self; descendants accepted (where testable) -----

    def test_every_exact_forbidden_path_rejects_itself(self):
        """Each exact-forbidden path must raise on its own."""
        for doc_path in sorted(_parse_anchor('forbidden-mount-exact')):
            with self.subTest(path=doc_path):
                with self.assertRaises(
                    manager.SandboxError,
                    msg=f'{doc_path}: documented exact-forbidden but validator accepted it',
                ):
                    manager._validate_workspace_path(str(_expand(doc_path)))

    def test_testable_exact_forbidden_paths_accept_a_real_descendant(self):
        """Each EXACT-forbidden path's documented semantic includes "descendants allowed."

        For the entries we can portably exercise, create a real
        directory under the path and confirm the validator accepts it.
        This is the regression test for the original ``~/.kato`` bug:
        if someone re-adds ``~/.kato`` to the SUBTREE set, the
        ``~/.kato/workspaces/<task>/<repo>`` path will start raising
        and this test will fail with a message that names the bug.
        """
        # CI portability: if Path.home() itself is under a subtree-
        # forbidden root (e.g., $HOME=/root on a container running as
        # root), every descendant of ~ would correctly be rejected
        # by the validator. Skip the ~ case rather than fail noisily
        # on a test-environment quirk; ~/.kato is unaffected since
        # /root/.kato isn't subsumed unless /root itself is the home.
        forbidden_ancestor_of_home = _home_is_under_subtree_forbidden()
        exact_paths = _parse_anchor('forbidden-mount-exact')
        for doc_path in sorted(exact_paths & _TESTABLE_EXACT_DESCENDANTS):
            with self.subTest(path=doc_path):
                base = _expand(doc_path)
                if forbidden_ancestor_of_home is not None and \
                        (base == Path.home() or
                         base.is_relative_to(forbidden_ancestor_of_home)):
                    self.skipTest(
                        f'Path.home() ({Path.home()}) is under subtree-'
                        f'forbidden {forbidden_ancestor_of_home}; '
                        f'descendants of {doc_path} are correctly '
                        f'rejected by the validator on this host. '
                        f'Test environment quirk, not a code bug.'
                    )
                # Pick a descendant location that is itself a
                # legitimate workspace clone shape. For ``~/.kato``
                # this is the canonical default workspace path.
                if doc_path == '~/.kato':
                    descendant = base / 'workspaces' / 'KATO-INV-TEST' / 'somerepo'
                else:
                    descendant = base / 'kato-invariant-test-tmp'
                descendant.mkdir(parents=True, exist_ok=True)
                # Track the topmost-newly-created ancestor for cleanup.
                # For ``~/.kato``, base may already exist (the audit
                # log lives there). For ``~``, base definitely exists.
                # Walk up from descendant until we hit an ancestor
                # that existed before we started.
                cleanup_target = descendant
                while cleanup_target.parent != base and cleanup_target.parent.exists():
                    cleanup_target = cleanup_target.parent
                self._cleanup.append(cleanup_target)
                try:
                    out = manager._validate_workspace_path(str(descendant))
                except manager.SandboxError as exc:
                    self.fail(
                        f'{doc_path}: documented as exact-only forbidden '
                        f'(descendants allowed), but validator rejected '
                        f'descendant {descendant}: {exc}. '
                        'Either move this entry to the subtree set OR '
                        'fix the validator to allow descendants.'
                    )
                self.assertEqual(Path(out), descendant.resolve())

    # ----- sanity: the two sets must be disjoint -----

    def test_subtree_and_exact_sets_are_disjoint(self):
        """No entry should appear in both anchor blocks.

        A path in both sets is meaningless (subtree subsumes exact),
        and would make the documented semantic ambiguous.
        """
        subtree = _parse_anchor('forbidden-mount-subtree')
        exact = _parse_anchor('forbidden-mount-exact')
        overlap = sorted(subtree & exact)
        self.assertFalse(
            overlap,
            f'these paths appear in BOTH subtree and exact anchor blocks: '
            f'{overlap}. Pick one — subtree means "path + descendants", '
            f'exact means "path only, descendants allowed". The two '
            f'are mutually exclusive.',
        )


if __name__ == '__main__':
    unittest.main()

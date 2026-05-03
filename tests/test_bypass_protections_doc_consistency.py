"""Drift guard: BYPASS_PROTECTIONS.md must match kato/sandbox/manager.py.

NOTE: this filename is a holdover from the bypass-only era. Under the
two-flag (``KATO_CLAUDE_DOCKER`` + ``KATO_CLAUDE_BYPASS_PERMISSIONS``)
model, the doc this test guards covers the sandbox in general, not
bypass mode specifically. The right name is
``test_sandbox_protections_doc_consistency.py``. Renaming now would
invalidate every existing reference (CI configs, prior PR
descriptions, blame links); defer to the next test-file
reorganization.

Both files are security-relevant. If they drift apart, the documented
threat model is no longer the implemented one — and the doc-as-control
discipline silently breaks. This test catches drift the moment it
lands in CI.

How it works:

* Each security-relevant invariant has a single source of truth as a
  ``frozenset`` constant in ``manager.py`` (e.g. ``_REQUIRED_DOCKER_FLAGS``).
* ``BYPASS_PROTECTIONS.md`` carries a matching anchored block of the
  form::

      <!-- SECURITY-INVARIANTS:<group>:BEGIN -->
      - item
      - item
      <!-- SECURITY-INVARIANTS:<group>:END -->

* The test asserts SET-EQUALITY (bidirectional) between the constant
  and the parsed anchor block. Failure messages name the file each
  side belongs to and the missing/extra items, so the next person to
  see CI red knows exactly what to fix.
* For required / forbidden Docker flags, the test additionally verifies
  the actual ``wrap_command`` argv applies / does not apply each flag
  — catching the case where someone removes a flag from
  ``wrap_command`` but leaves the constant in place.

This test does NOT need Docker. ``_image_digest_strict`` is patched to
return a fake digest so ``wrap_command`` can be constructed without
hitting the daemon.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest import mock

from kato_core_lib.sandbox import manager


_DOC_PATH = Path(__file__).resolve().parent.parent / 'BYPASS_PROTECTIONS.md'

_ANCHOR_RE = re.compile(
    r'<!--\s*SECURITY-INVARIANTS:(?P<group>[a-z0-9-]+):BEGIN\s*-->\s*\n'
    r'(?P<body>.*?)'
    r'\n\s*<!--\s*SECURITY-INVARIANTS:\1:END\s*-->',
    re.DOTALL,
)
_ITEM_RE = re.compile(r'^\s*-\s+(.+?)\s*$', re.MULTILINE)


def _parse_anchor(group: str) -> set[str]:
    """Return the set of bullet items inside the named anchor block.

    Raises ``AssertionError`` if the anchor block is missing — that
    failure on its own is informative ("you removed the doc anchor")
    and points the maintainer at the right file.
    """
    text = _DOC_PATH.read_text(encoding='utf-8')
    for match in _ANCHOR_RE.finditer(text):
        if match.group('group') == group:
            return {
                item.strip()
                for item in _ITEM_RE.findall(match.group('body'))
                if item.strip()
            }
    raise AssertionError(
        f'no anchor block named {group!r} found in {_DOC_PATH}. '
        f'Expected: <!-- SECURITY-INVARIANTS:{group}:BEGIN --> ... '
        f'<!-- SECURITY-INVARIANTS:{group}:END -->. '
        f'Did you delete the wrong section?'
    )


def _normalize_path_for_doc(path: Path) -> str:
    """``Path.home() / '.ssh'`` → ``'~/.ssh'`` for comparison with the doc."""
    home = Path.home()
    try:
        rel = path.relative_to(home)
    except ValueError:
        return str(path)
    return '~' if str(rel) == '.' else f'~/{rel}'


def _flag_in_argv(argv: list[str], flag: str) -> bool:
    """True if ``flag`` (in either single-token or two-token argv form) is present.

    Recognises both forms because Docker accepts both:

    * ``--key=value`` as a single token, or
    * ``--key value`` as two adjacent tokens.

    Boolean flags (``--read-only``) are matched verbatim.
    """
    if '=' in flag:
        # The argv builder may have written either form; check both.
        if flag in argv:
            return True
        key, value = flag.split('=', 1)
        for i, token in enumerate(argv):
            if token == key and i + 1 < len(argv) and argv[i + 1] == value:
                return True
        return False
    return flag in argv


def _build_argv() -> list[str]:
    """Construct a representative ``wrap_command`` argv for inspection.

    Stubs ``_image_digest_strict`` so the test doesn't need a running
    Docker daemon. Uses a temp directory under ``$HOME`` as the
    workspace so ``_validate_workspace_path`` is satisfied.
    """
    import tempfile
    fake_digest = 'sha256:' + 'a' * 64
    with tempfile.TemporaryDirectory(dir=Path.home()) as workspace:
        with mock.patch.object(
            manager, '_image_digest_strict', return_value=fake_digest,
        ):
            return manager.wrap_command(
                ['claude', '-p', 'drift-guard'],
                workspace_path=workspace,
                container_name='kato-sandbox-drift-aaaaa001',
                task_id='DRIFT-GUARD',
            )


class BypassProtectionsDocConsistencyTests(unittest.TestCase):
    """Set-equality between manager.py constants and BYPASS_PROTECTIONS.md anchors."""

    def _assert_sets_equal(self, code_set, doc_set, label):
        """Bidirectional set-equality with a useful diff message."""
        code_only = sorted(code_set - doc_set)
        doc_only = sorted(doc_set - code_set)
        if not code_only and not doc_only:
            return
        msg_lines = [
            f'{label}: manager.py and BYPASS_PROTECTIONS.md disagree.',
        ]
        if code_only:
            msg_lines.append(
                f'  In manager.py but NOT in BYPASS_PROTECTIONS.md: {code_only}'
            )
            msg_lines.append(
                '  Fix: add to the matching <!-- SECURITY-INVARIANTS:... --> '
                'anchor in BYPASS_PROTECTIONS.md, OR remove from manager.py.'
            )
        if doc_only:
            msg_lines.append(
                f'  In BYPASS_PROTECTIONS.md but NOT in manager.py: {doc_only}'
            )
            msg_lines.append(
                '  Fix: add to the matching constant in manager.py, OR remove '
                'from the BYPASS_PROTECTIONS.md anchor.'
            )
        self.fail('\n'.join(msg_lines))

    # ---- set-equality tests for each anchor group ----

    def test_required_docker_flags_match(self):
        self._assert_sets_equal(
            set(manager._REQUIRED_DOCKER_FLAGS),
            _parse_anchor('required-docker-flags'),
            'required Docker run flags',
        )

    def test_forbidden_docker_flags_match(self):
        self._assert_sets_equal(
            set(manager._FORBIDDEN_DOCKER_FLAGS),
            _parse_anchor('forbidden-docker-flags'),
            'forbidden Docker run flags',
        )

    def test_forbidden_mount_subtree_match(self):
        code = {_normalize_path_for_doc(p) for p in manager._FORBIDDEN_MOUNT_SOURCES_SUBTREE}
        self._assert_sets_equal(
            code,
            _parse_anchor('forbidden-mount-subtree'),
            'forbidden workspace mount roots (subtree)',
        )

    def test_forbidden_mount_exact_match(self):
        code = {_normalize_path_for_doc(p) for p in manager._FORBIDDEN_MOUNT_SOURCES_EXACT}
        self._assert_sets_equal(
            code,
            _parse_anchor('forbidden-mount-exact'),
            'forbidden workspace mount roots (exact)',
        )

    def test_auth_volume_invariants_match(self):
        self._assert_sets_equal(
            set(manager._AUTH_VOLUME_INVARIANTS),
            _parse_anchor('auth-volume-invariants'),
            'auth-volume invariants',
        )

    def test_firewall_guarantees_match(self):
        self._assert_sets_equal(
            set(manager._FIREWALL_GUARANTEES),
            _parse_anchor('firewall-guarantees'),
            'firewall guarantees',
        )

    def test_classification_terms_match(self):
        self._assert_sets_equal(
            set(manager._CLASSIFICATION_TERMS),
            _parse_anchor('classification-terms'),
            'threat-model classification terms',
        )

    # ---- semantic enforcement: the flags must actually be applied ----

    def test_required_flags_actually_applied_by_wrap_command(self):
        """Catches the 'removed flag from wrap_command, forgot the constant' regression."""
        argv = _build_argv()
        missing = sorted(
            flag for flag in manager._REQUIRED_DOCKER_FLAGS
            if not _flag_in_argv(argv, flag)
        )
        self.assertFalse(
            missing,
            f'wrap_command argv is missing required security flags '
            f'(declared in manager._REQUIRED_DOCKER_FLAGS but not actually '
            f'emitted by wrap_command): {missing}. Either restore the flag '
            f'in wrap_command, or — if the change was deliberate — remove it '
            f'from _REQUIRED_DOCKER_FLAGS AND from the matching anchor in '
            f'BYPASS_PROTECTIONS.md, AND update the relevant table row(s) '
            f'to reflect the downgraded threat-model status.',
        )

    def test_forbidden_flags_never_applied_by_wrap_command(self):
        """Catches the 'someone added an unsafe flag' regression."""
        argv = _build_argv()
        present = sorted(
            flag for flag in manager._FORBIDDEN_DOCKER_FLAGS
            if _flag_in_argv(argv, flag)
        )
        self.assertFalse(
            present,
            f'wrap_command argv contains FORBIDDEN security flag(s): {present}. '
            f'Each of these silently downgrades the threat model — see the '
            f'"Why these specific surfaces" section of BYPASS_PROTECTIONS.md '
            f'for the per-flag rationale. Either remove the flag, or '
            f'(if intentional) remove it from _FORBIDDEN_DOCKER_FLAGS with '
            f'documented justification — that change requires a security '
            f'review, not just a refactor.',
        )


if __name__ == '__main__':
    unittest.main()

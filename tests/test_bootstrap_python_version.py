"""Bootstrap must refuse an interpreter older than ``requires-python``.

Reported from Pop!_OS: ``./kato up`` bootstrapped under the distro ``python3``
(3.10) while pyproject requires >= 3.11. The visible failure was neither of
those facts — the venv step died with the stdlib's Debian/Ubuntu message
naming ``apt install python3.10-venv``, i.e. the venv package for the WRONG
interpreter. Installing it would have built the venv and moved the wall to
``pip install -e .`` rejecting the project on ``requires-python``.

Two guards, pinned here: the version check itself, and the launcher no longer
hardcoding ``python3``.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
from _script_utils import MIN_PYTHON, require_supported_python  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


def _requires_python(pyproject: Path) -> tuple[int, ...]:
    """Parse ``requires-python = ">=X.Y"`` without tomllib.

    tomllib is 3.11+, and the whole point of this check is to run on older
    interpreters, so the constant it guards cannot depend on it.
    """
    match = re.search(
        r'^requires-python\s*=\s*["\']>=\s*([0-9]+)\.([0-9]+)',
        pyproject.read_text(encoding='utf-8'),
        re.MULTILINE,
    )
    assert match, f'no requires-python in {pyproject}'
    return (int(match.group(1)), int(match.group(2)))


class MinPythonMatchesPackagingTests(unittest.TestCase):
    """The literal and the packaging metadata are one fact in two files."""

    def test_matches_the_root_pyproject(self) -> None:
        self.assertEqual(MIN_PYTHON, _requires_python(REPO_ROOT / 'pyproject.toml'))

    def test_matches_the_webserver_pyproject(self) -> None:
        # Bootstrap installs this one too, so a lower floor here would fail
        # the install after the version check had already passed.
        self.assertEqual(
            MIN_PYTHON, _requires_python(REPO_ROOT / 'webserver' / 'pyproject.toml'),
        )


class RequireSupportedPythonTests(unittest.TestCase):

    def test_the_interpreter_running_this_suite_is_accepted(self) -> None:
        require_supported_python()  # must not raise

    def test_an_older_interpreter_is_refused_with_both_halves_of_the_fix(self) -> None:
        older = (3, 10, 12, 'final', 0)
        with mock.patch.object(sys, 'version_info', older):
            with mock.patch('sys.stderr') as stderr:
                with self.assertRaises(SystemExit) as raised:
                    require_supported_python()

        self.assertEqual(raised.exception.code, 1)
        printed = ''.join(
            str(call.args[0]) for call in stderr.write.call_args_list if call.args
        )
        minimum = '.'.join(str(part) for part in MIN_PYTHON)
        # It must name the interpreter to USE...
        self.assertIn(f'python{minimum}', printed)
        # ...and warn off the tempting wrong fix, which is the apt package
        # the stdlib's own venv error points you at.
        self.assertIn('3.10', printed)
        self.assertIn('Do NOT', printed)


class LauncherPicksASupportedInterpreterTests(unittest.TestCase):
    """``./kato`` used to ``exec python3`` whatever that was."""

    def setUp(self) -> None:
        self.launcher = (REPO_ROOT / 'kato').read_text(encoding='utf-8')

    def test_it_does_not_hardcode_bare_python3(self) -> None:
        self.assertNotIn(
            'exec python3 "$SCRIPT_DIR', self.launcher,
            'the launcher is back to using whatever python3 is on PATH',
        )

    def test_it_gates_candidates_on_the_minimum_version(self) -> None:
        minimum = f'({MIN_PYTHON[0]}, {MIN_PYTHON[1]})'
        self.assertIn(minimum, self.launcher)

    def test_it_offers_an_override_and_a_real_error(self) -> None:
        self.assertIn('KATO_PYTHON', self.launcher)
        self.assertIn('-venv', self.launcher)


if __name__ == '__main__':
    unittest.main()

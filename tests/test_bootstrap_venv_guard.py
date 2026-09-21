"""Bootstrap must refuse a venv that has no pip, and say how to fix it.

Reported from Linux: ``python -m venv .venv`` died with the stdlib's
Debian/Ubuntu message (``ensurepip is not available`` / ``Failing command:
.../.venv/bin/python3``), which means the ``pythonX.Y-venv`` package is not
installed.

The follow-on defect is ours. ``venv`` creates ``bin/python`` BEFORE it
bootstraps pip, so the failed run leaves an interpreter behind; an
``exists()``-only check then SKIPS creation on the next run and the failure
resurfaces at ``pip install`` as ``No module named pip`` — naming neither venv
nor the package to install.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_bootstrap():
    """Import ``scripts/bootstrap.py`` by path (it is a script, not a module)."""
    sys.path.insert(0, str(REPO_ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location(
        'kato_bootstrap_under_test', REPO_ROOT / 'scripts' / 'bootstrap.py',
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VenvHasPipTests(unittest.TestCase):
    """``_venv_has_pip`` is the check that replaced a bare ``exists()``."""

    def setUp(self) -> None:
        self.bootstrap = _load_bootstrap()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)

    def test_a_missing_interpreter_is_not_usable(self) -> None:
        self.assertFalse(
            self.bootstrap._venv_has_pip(self.tmp_root / 'bin' / 'python'),
        )

    def test_the_interpreter_running_this_suite_has_pip(self) -> None:
        # Positive control: the probe must be capable of returning True, or
        # the negative cases below would pass for the wrong reason.
        self.assertTrue(self.bootstrap._venv_has_pip(Path(sys.executable)))

    def test_a_file_that_is_not_an_interpreter_is_not_usable(self) -> None:
        # The failure mode is an unrunnable path, which must be reported as
        # "no pip" rather than raised as OSError out of bootstrap.
        fake = self.tmp_root / 'python'
        fake.write_text('not an interpreter\n')
        self.assertFalse(self.bootstrap._venv_has_pip(fake))

    def test_the_hint_names_the_package_the_directory_and_the_version(self) -> None:
        hint = self.bootstrap._venv_repair_hint()
        version = f'{sys.version_info.major}.{sys.version_info.minor}'
        # The package, version-matched — a bare python3-venv can target a
        # different interpreter than the one running bootstrap.
        self.assertIn(f'python{version}-venv', hint)
        # AND the leftover directory: installing the package alone does not
        # help while the half-built venv is still on disk.
        self.assertIn('rm -rf', hint)
        self.assertIn(str(self.bootstrap.VENV_DIR), hint)


class RealVenvWithoutPipTests(unittest.TestCase):
    """End-to-end against a REAL pip-less venv, not a stubbed one.

    ``--without-pip`` reproduces exactly what the operator was left holding:
    a venv whose ``bin/python`` exists and runs, with no pip inside it.
    """

    def setUp(self) -> None:
        self.bootstrap = _load_bootstrap()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.venv_dir = Path(self._tmp.name) / '.venv'
        created = subprocess.run(
            [sys.executable, '-m', 'venv', '--without-pip', str(self.venv_dir)],
            capture_output=True,
        )
        if created.returncode != 0:
            self.skipTest('this interpreter cannot create a venv at all')
        self.python_bin = (
            self.venv_dir / 'Scripts' / 'python.exe'
            if sys.platform == 'win32'
            else self.venv_dir / 'bin' / 'python'
        )

    def test_the_interpreter_exists_which_is_why_exists_was_not_enough(self) -> None:
        # This is the whole point: the old check would have returned early
        # here and handed a pip-less interpreter to the install step.
        self.assertTrue(self.python_bin.exists())

    def test_but_it_is_reported_as_unusable(self) -> None:
        self.assertFalse(self.bootstrap._venv_has_pip(self.python_bin))

    def test_ensure_venv_stops_with_the_hint_instead_of_pressing_on(self) -> None:
        # The behaviour change itself: handed the operator's leftover, the
        # step exits non-zero carrying the fix, rather than returning early
        # and letting the next step die on ``No module named pip``.
        self.bootstrap.venv_python_path = lambda: self.python_bin
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                self.bootstrap._ensure_venv()

        self.assertEqual(raised.exception.code, 1)
        printed = stderr.getvalue()
        version = f'{sys.version_info.major}.{sys.version_info.minor}'
        self.assertIn(f'python{version}-venv', printed)
        self.assertIn('rm -rf', printed)

    def test_ensure_venv_returns_quietly_when_the_venv_is_healthy(self) -> None:
        # Guard against a fix that refuses every venv: the interpreter
        # running this suite has pip, so it must be accepted silently.
        self.bootstrap.venv_python_path = lambda: Path(sys.executable)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.bootstrap._ensure_venv()

        self.assertEqual(stderr.getvalue(), '')


if __name__ == '__main__':
    unittest.main()

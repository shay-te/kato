"""Rebuilding ``kato.exe`` while kato is running.

Reported on Windows: ``tools/kato/build.py`` failed with ``PermissionError:
[WinError 5] Access is denied: 'C:\\Codes\\kato\\kato.exe'``. PyInstaller was
told to write straight into the repo root, deletes the existing binary first,
and Windows refuses to delete a running ``.exe``. It does allow RENAMING one —
which is what the install step now relies on.

Windows' rule is simulated with a ``replace`` that refuses to overwrite a locked
file, lets a locked file be renamed (the lock follows it), and a ``remove`` that
refuses locked files. Real files in a temp directory throughout.
"""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_BUILD_PY = Path(__file__).resolve().parent.parent / 'tools' / 'kato' / 'build.py'
_spec = importlib.util.spec_from_file_location('kato_exe_build', _BUILD_PY)
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)


class _WindowsFiles:
    """``os.replace`` / ``os.remove`` with Windows' rules for a running binary."""

    def __init__(self, running=(), unrenamable=()):
        self.running = {str(path) for path in running}
        self.unrenamable = {str(path) for path in unrenamable}

    def replace(self, source, destination):
        if str(destination) in self.running:
            raise PermissionError(13, 'Access is denied', str(destination))
        if str(source) in self.unrenamable:
            raise PermissionError(13, 'Access is denied', str(source))
        os.replace(source, destination)
        if str(source) in self.running:
            self.running.discard(str(source))
            self.running.add(str(destination))

    def remove(self, path):
        if str(path) in self.running:
            raise PermissionError(13, 'Access is denied', str(path))
        os.remove(path)


class InstallBinaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        root = Path(self._dir.name)
        self.built = root / 'build' / 'dist' / 'kato.exe'
        self.built.parent.mkdir(parents=True)
        self.built.write_text('new build', encoding='utf-8')
        self.target = root / 'kato.exe'

    def _install(self, files):
        return build._install_binary(
            self.built, self.target, replace=files.replace, remove=files.remove,
        )

    def test_a_first_build_just_installs(self) -> None:
        self.assertIsNone(self._install(_WindowsFiles()))
        self.assertEqual(self.target.read_text(encoding='utf-8'), 'new build')

    def test_an_idle_binary_is_simply_replaced(self) -> None:
        self.target.write_text('old build', encoding='utf-8')
        self.assertIsNone(self._install(_WindowsFiles()))
        self.assertEqual(self.target.read_text(encoding='utf-8'), 'new build')
        self.assertEqual(list(self.target.parent.glob('kato.exe.old*')), [])

    def test_a_running_binary_is_moved_aside_instead_of_failing(self) -> None:
        self.target.write_text('old build', encoding='utf-8')
        moved = self._install(_WindowsFiles(running=[self.target]))
        self.assertEqual(moved, self.target.with_name('kato.exe.old'))
        self.assertEqual(self.target.read_text(encoding='utf-8'), 'new build')
        self.assertEqual(moved.read_text(encoding='utf-8'), 'old build')

    def test_the_previous_moved_aside_copy_is_cleaned_up_once_free(self) -> None:
        stale = self.target.with_name('kato.exe.old')
        stale.write_text('older build', encoding='utf-8')
        self.target.write_text('old build', encoding='utf-8')
        self.assertIsNone(self._install(_WindowsFiles()))
        self.assertFalse(stale.exists())

    def test_a_moved_aside_copy_still_running_is_left_and_a_new_name_used(self) -> None:
        stale = self.target.with_name('kato.exe.old')
        stale.write_text('older build, still running', encoding='utf-8')
        self.target.write_text('old build', encoding='utf-8')
        moved = self._install(_WindowsFiles(running=[stale, self.target]))
        self.assertEqual(moved, self.target.with_name('kato.exe.old1'))
        self.assertTrue(stale.exists())
        self.assertEqual(self.target.read_text(encoding='utf-8'), 'new build')

    def test_a_binary_that_cannot_even_be_renamed_raises(self) -> None:
        self.target.write_text('old build', encoding='utf-8')
        files = _WindowsFiles(running=[self.target], unrenamable=[self.target])
        with self.assertRaises(PermissionError):
            self._install(files)


class BuildNeverWritesIntoTheRepoRootTests(unittest.TestCase):
    def test_pyinstaller_writes_into_the_work_directory(self) -> None:
        with patch.object(build.subprocess, 'check_call') as check_call:
            build._run_pyinstaller(Path('python'))
        command = check_call.call_args.args[0]
        distpath = Path(command[command.index('--distpath') + 1])
        self.assertEqual(distpath, build.DIST_DIR)
        self.assertNotEqual(distpath, build.REPO_ROOT)

    def test_a_locked_binary_ends_the_build_with_a_clear_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'kato.py').write_text('', encoding='utf-8')
            python = root / 'python'
            python.write_text('', encoding='utf-8')
            dist = root / 'build' / 'make-exe' / 'dist'
            dist.mkdir(parents=True)
            (dist / build._binary_name()).write_text('new build', encoding='utf-8')
            with patch.multiple(
                build, KATO_PY=root / 'kato.py', REPO_ROOT=root,
                WORK_DIR=root / 'build' / 'make-exe', DIST_DIR=dist,
            ), patch.object(build, '_venv_python', return_value=python), \
                    patch.object(build, '_ensure_pyinstaller'), \
                    patch.object(build, '_run_pyinstaller'), \
                    patch.object(build, '_install_binary', side_effect=PermissionError('held')), \
                    patch('sys.stderr') as stderr:
                self.assertEqual(build.main(), 1)
            written = ''.join(call.args[0] for call in stderr.write.call_args_list)
            self.assertIn('Close every running kato', written)
            self.assertFalse((root / 'build').exists())


if __name__ == '__main__':
    unittest.main()

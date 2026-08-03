"""The Windows npm shim bypass, pinned — including the shapes a fork missed.

Two transports each had a copy of this and they were not equivalent. The Codex
copy handled the JS-entry-point shape with a ``%~dp0`` prefix and nothing else,
so a native-binary shim, a ``%dp0%`` prefix, or a nested reference all fell
back to spawning ``cmd.exe`` — where a multi-line ``--append-system-prompt``
silently truncates the command line and the resume flags vanish. No error, just
an agent with no memory of its own session.

The MISSED-SHAPE tests below are the point of this file: each one fails against
the narrower copy.

Every test drives the un-gated ``resolve_cli_shim_invocation`` so the real
parsing runs on a POSIX host. Patching ``os.name`` instead would send
``pathlib.Path()`` to ``WindowsPath`` and crash before reaching the logic.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_core_lib.agent_core_lib.helpers.cli_shim_utils import (
    is_windows_host,
    read_shim_text,
    resolve_cli_shim_invocation,
    resolve_node_binary,
    resolve_shim_reference,
    resolve_windows_cli_invocation,
)

MODULE = 'agent_core_lib.agent_core_lib.helpers.cli_shim_utils'


class ShimFixture(unittest.TestCase):
    """A temp directory standing in for an npm ``node_modules/.bin``."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.bin_dir = Path(self._tmp.name)

    def write_shim(self, body: str, name: str = 'agent.cmd') -> Path:
        shim = self.bin_dir / name
        shim.write_text(body)
        return shim

    def touch(self, relative: str) -> Path:
        target = self.bin_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('#!/usr/bin/env node\n')
        return target


class HostGateTests(unittest.TestCase):
    def test_non_windows_host_never_rewrites_the_invocation(self) -> None:
        with patch(f'{MODULE}.is_windows_host', return_value=False):
            self.assertIsNone(resolve_windows_cli_invocation('/usr/bin/agent.cmd'))

    def test_windows_host_delegates_to_the_parser(self) -> None:
        with patch(f'{MODULE}.is_windows_host', return_value=True), \
             patch(f'{MODULE}.resolve_cli_shim_invocation',
                   return_value=['node.exe', 'cli.js']) as parser:
            self.assertEqual(
                resolve_windows_cli_invocation('C:/bin/agent.cmd'),
                ['node.exe', 'cli.js'],
            )
        parser.assert_called_once_with('C:/bin/agent.cmd')

    def test_is_windows_host_reads_os_name(self) -> None:
        with patch(f'{MODULE}.os.name', 'nt'):
            self.assertTrue(is_windows_host())
        with patch(f'{MODULE}.os.name', 'posix'):
            self.assertFalse(is_windows_host())


class MissedShapeTests(ShimFixture):
    """Shapes the narrower fork fell back on. Each is a real npm layout."""

    def test_native_binary_shim_resolves_without_needing_node(self) -> None:
        # Codex ships a native binary through npm, so THIS is the shape its
        # own shim uses — and the fork that only looked for ``.js`` never
        # matched it. ``which('node')`` returning None proves no Node is
        # consulted for this shape.
        exe = self.touch('node_modules/@vendor/agent/bin/agent.exe')
        shim = self.write_shim(r'"%~dp0\node_modules\@vendor\agent\bin\agent.exe" %*')
        with patch(f'{MODULE}.shutil.which', return_value=None):
            self.assertEqual(resolve_cli_shim_invocation(str(shim)), [str(exe.resolve())])

    def test_setlocal_dp0_prefix_resolves(self) -> None:
        # Newer npm shims do ``SET dp0=%~dp0`` and then reference ``%dp0%``.
        js = self.touch('cli.js')
        shim = self.write_shim('SET dp0=%~dp0\r\n"%dp0%\\cli.js" %*')
        node = self.bin_dir / 'node.exe'
        node.write_text('')
        self.assertEqual(
            resolve_cli_shim_invocation(str(shim)),
            [str(node), str(js.resolve())],
        )

    def test_nested_backslash_reference_resolves(self) -> None:
        # Without separator normalization the whole 'a\b\cli.js' is read as ONE
        # literal filename on a POSIX host and never matches the file.
        js = self.touch('node_modules/agent/lib/cli.js')
        shim = self.write_shim(r'"%~dp0\node_modules\agent\lib\cli.js" %*')
        node = self.bin_dir / 'node.exe'
        node.write_text('')
        self.assertEqual(
            resolve_cli_shim_invocation(str(shim)),
            [str(node), str(js.resolve())],
        )


class JsEntryPointTests(ShimFixture):
    def test_forward_slash_reference_resolves(self) -> None:
        js = self.touch('cli.js')
        shim = self.write_shim('"%~dp0/cli.js" %*')
        node = self.bin_dir / 'node.exe'
        node.write_text('')
        self.assertEqual(
            resolve_cli_shim_invocation(str(shim)),
            [str(node), str(js.resolve())],
        )

    def test_node_beside_the_shim_wins_over_path(self) -> None:
        # The shim's own Node is the version the CLI was installed against.
        self.touch('cli.js')
        shim = self.write_shim('"%~dp0/cli.js" %*')
        local_node = self.bin_dir / 'node.exe'
        local_node.write_text('')
        with patch(f'{MODULE}.shutil.which', return_value='/usr/local/bin/node'):
            argv = resolve_cli_shim_invocation(str(shim))
        self.assertEqual(argv[0], str(local_node))

    def test_falls_back_to_node_on_path(self) -> None:
        js = self.touch('cli.js')
        shim = self.write_shim('"%~dp0/cli.js" %*')
        with patch(f'{MODULE}.shutil.which', return_value='/usr/local/bin/node'):
            self.assertEqual(
                resolve_cli_shim_invocation(str(shim)),
                ['/usr/local/bin/node', str(js.resolve())],
            )

    def test_no_node_anywhere_falls_back_to_the_shim(self) -> None:
        self.touch('cli.js')
        shim = self.write_shim('"%~dp0/cli.js" %*')
        with patch(f'{MODULE}.shutil.which', return_value=None):
            self.assertIsNone(resolve_cli_shim_invocation(str(shim)))


class FallBackToTheShimTests(ShimFixture):
    """Every path that must return None rather than guess."""

    def test_non_shim_suffix(self) -> None:
        self.assertIsNone(resolve_cli_shim_invocation('/usr/bin/agent'))
        self.assertIsNone(resolve_cli_shim_invocation('C:/bin/agent.exe'))

    def test_bat_suffix_is_also_a_shim(self) -> None:
        js = self.touch('cli.js')
        shim = self.write_shim('"%~dp0/cli.js" %*', name='agent.bat')
        node = self.bin_dir / 'node.exe'
        node.write_text('')
        self.assertEqual(
            resolve_cli_shim_invocation(str(shim)),
            [str(node), str(js.resolve())],
        )

    def test_unreadable_shim(self) -> None:
        shim = self.write_shim('"%~dp0/cli.js" %*')
        with patch.object(Path, 'read_text', side_effect=OSError('locked')):
            self.assertIsNone(resolve_cli_shim_invocation(str(shim)))

    def test_shim_with_no_quoted_target(self) -> None:
        shim = self.write_shim('@echo off\r\necho nothing to see\r\n')
        self.assertIsNone(resolve_cli_shim_invocation(str(shim)))

    def test_js_target_does_not_exist(self) -> None:
        shim = self.write_shim('"%~dp0/missing.js" %*')
        self.assertIsNone(resolve_cli_shim_invocation(str(shim)))

    def test_exe_target_missing_falls_through_to_the_js_shape(self) -> None:
        # A shim naming both (npm emits a ``.exe`` probe with a ``.js``
        # fallback) must not give up just because the exe isn't installed.
        js = self.touch('cli.js')
        shim = self.write_shim('"%~dp0/gone.exe" || "%~dp0/cli.js" %*')
        node = self.bin_dir / 'node.exe'
        node.write_text('')
        self.assertEqual(
            resolve_cli_shim_invocation(str(shim)),
            [str(node), str(js.resolve())],
        )

    def test_exe_target_missing_and_no_js_returns_none(self) -> None:
        shim = self.write_shim('"%~dp0/gone.exe" %*')
        self.assertIsNone(resolve_cli_shim_invocation(str(shim)))


class HelperTests(ShimFixture):
    def test_read_shim_text_returns_none_on_oserror(self) -> None:
        self.assertIsNone(read_shim_text(self.bin_dir / 'no-such-file.cmd'))

    def test_read_shim_text_tolerates_undecodable_bytes(self) -> None:
        shim = self.bin_dir / 'agent.cmd'
        shim.write_bytes(b'"%~dp0/cli.js" \xff\xfe %*')
        self.assertIn('cli.js', read_shim_text(shim))

    def test_resolve_shim_reference_returns_none_for_a_directory(self) -> None:
        (self.bin_dir / 'sub').mkdir()
        self.assertIsNone(resolve_shim_reference('sub', self.bin_dir))

    def test_resolve_shim_reference_without_any_prefix_token(self) -> None:
        target = self.touch('cli.js')
        self.assertEqual(resolve_shim_reference('cli.js', self.bin_dir), target.resolve())

    def test_resolve_node_binary_prefers_local(self) -> None:
        local = self.bin_dir / 'node.exe'
        local.write_text('')
        self.assertEqual(resolve_node_binary(self.bin_dir), local)

    def test_resolve_node_binary_returns_none_when_absent_everywhere(self) -> None:
        with patch(f'{MODULE}.shutil.which', return_value=None):
            self.assertIsNone(resolve_node_binary(self.bin_dir))


if __name__ == '__main__':
    unittest.main()

"""Shut down inside the desktop app: the app quits with its kato.

The app reads every line its kato writes; a graceful shutdown of a kato it
started (``KATO_DESKTOP_APP``) says ``KATO-QUIT``, and the app then quits when
the process ends instead of reporting a service that stopped. The two sides
are separate languages, so the marker and the variable are pinned equal here.
"""

from __future__ import annotations

import io
import os
import re
import signal
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kato_core_lib import main as main_module

DESKTOP = Path(__file__).resolve().parents[1] / 'desktop' / 'src-tauri' / 'src'


class DesktopQuitMarkerTests(unittest.TestCase):
    def _shut_down(self, env: dict) -> str:
        app = SimpleNamespace(service=MagicMock(), logger=MagicMock())
        written = io.StringIO()
        original = signal.getsignal(signal.SIGINT)
        try:
            main_module._register_shutdown_hook(app)
            with patch.dict(os.environ, env, clear=False), patch.object(main_module, '_exit_now'), \
                    patch.object(main_module, '_emit_raw', side_effect=written.write):
                if not env:
                    os.environ.pop(main_module.DESKTOP_APP_ENV_KEY, None)
                signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        finally:
            signal.signal(signal.SIGINT, original)
        return written.getvalue()

    def test_a_kato_the_desktop_app_started_says_it_is_quitting(self) -> None:
        said = self._shut_down({main_module.DESKTOP_APP_ENV_KEY: '1'})
        self.assertIn('stopping kato', said)
        self.assertIn(f'\n{main_module.DESKTOP_QUIT_MARKER}\n', said)

    def test_a_terminal_kato_never_shows_the_marker(self) -> None:
        said = self._shut_down({})
        self.assertIn('stopping kato', said)
        self.assertNotIn(main_module.DESKTOP_QUIT_MARKER, said)

    def test_the_app_and_kato_agree_on_the_marker_and_the_variable(self) -> None:
        main_rs = (DESKTOP / 'main.rs').read_text(encoding='utf-8')
        wsl_rs = (DESKTOP / 'wsl.rs').read_text(encoding='utf-8')
        self.assertEqual(re.search(r'const QUIT_MARKER: &str = "([^"]+)";', main_rs).group(1),
                         main_module.DESKTOP_QUIT_MARKER)
        self.assertIn(f'.env("{main_module.DESKTOP_APP_ENV_KEY}", "1")', main_rs)   # a native kato
        self.assertIn(f'{main_module.DESKTOP_APP_ENV_KEY}=1', wsl_rs)               # a kato inside WSL
        self.assertIn('CommandEvent::Terminated(_) if quit_requested', main_rs)


if __name__ == '__main__':
    unittest.main()

"""Flow test: kato loads hooks.json at boot, refuses on schema errors.

Hook config is loaded inside :class:`KatoCoreLib.__init__` (early, so
sub-services like ``PlanningSessionRunner`` see a real runner instead
of None). ``_load_hooks_or_refuse`` in main.py is now a presence
check that guards against future refactors silently dropping hooks.

Pins four contracts:
  1. No file        → empty config, no log spam.
  2. Valid file     → hooks parsed, runner built, boot log emitted.
  3. Malformed file → HookConfigError, operator sees a clear error.
  4. Boot ordering  → main.py calls the guard before workspace recovery.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kato_core_lib import main as kato_main
from kato_core_lib.hooks.config import HookConfigError
from kato_core_lib.kato_core_lib import KatoCoreLib


class FlowHooksBootTests(unittest.TestCase):

    def test_no_hooks_file_leaves_empty_config(self) -> None:
        logger = MagicMock()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('KATO_HOOKS_CONFIG', None)
            config, runner = KatoCoreLib._load_hooks(logger)
        self.assertTrue(config.is_empty())
        self.assertIsNotNone(runner)
        # No "hooks loaded" line when nothing is configured.
        info_calls = [c.args[0] for c in logger.info.call_args_list if c.args]
        self.assertFalse(any('hooks loaded' in m for m in info_calls))

    def test_valid_file_is_loaded_and_logged(self) -> None:
        logger = MagicMock()
        with tempfile.NamedTemporaryFile(
            'w', suffix='.json', delete=False, encoding='utf-8',
        ) as fh:
            json.dump({
                'session_end': [{'command': 'curl webhook'}],
                'pre_tool_use': [
                    {'match': {'tool': 'Bash'}, 'command': 'audit'},
                ],
            }, fh)
            path = fh.name
        try:
            with patch.dict(os.environ, {'KATO_HOOKS_CONFIG': path}):
                config, runner = KatoCoreLib._load_hooks(logger)
        finally:
            os.unlink(path)
        self.assertFalse(config.is_empty())
        self.assertIsNotNone(runner)
        # Operator gets a boot log they can grep for.
        info_calls = [c.args[0] for c in logger.info.call_args_list if c.args]
        self.assertTrue(any('hooks loaded' in m for m in info_calls))

    def test_malformed_file_raises_hook_config_error(self) -> None:
        logger = MagicMock()
        with tempfile.NamedTemporaryFile(
            'w', suffix='.json', delete=False, encoding='utf-8',
        ) as fh:
            json.dump({'pre_tool': [{'command': 'x'}]}, fh)  # typo
            path = fh.name
        try:
            with patch.dict(os.environ, {'KATO_HOOKS_CONFIG': path}):
                with self.assertRaises(HookConfigError):
                    KatoCoreLib._load_hooks(logger)
        finally:
            os.unlink(path)
        # The error is logged so the boot trace shows the cause.
        logger.error.assert_called()
        error_msg = logger.error.call_args.args[0]
        self.assertIn('hooks config rejected', error_msg)

    def test_load_hooks_or_refuse_passes_through_when_runner_present(self) -> None:
        # KatoCoreLib already loaded hooks → guard is satisfied and
        # leaves the existing runner alone.
        existing_runner = MagicMock()
        app = SimpleNamespace(hook_runner=existing_runner, hooks_config=MagicMock())
        kato_main._load_hooks_or_refuse(app, MagicMock())
        self.assertIs(app.hook_runner, existing_runner)

    def test_load_hooks_or_refuse_lazy_loads_when_runner_missing(self) -> None:
        # Test setups that mock out KatoInstance.init leave the app
        # without a runner — the guard then loads hooks itself so
        # downstream code never sees ``hook_runner is None``.
        app = SimpleNamespace()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('KATO_HOOKS_CONFIG', None)
            kato_main._load_hooks_or_refuse(app, MagicMock())
        self.assertIsNotNone(app.hook_runner)
        self.assertTrue(app.hooks_config.is_empty())

    def test_load_hooks_or_refuse_refuses_on_malformed_config(self) -> None:
        # Schema error in hooks.json still kills the boot loud and
        # clear — even on the lazy-load path.
        app = SimpleNamespace()
        with tempfile.NamedTemporaryFile(
            'w', suffix='.json', delete=False, encoding='utf-8',
        ) as fh:
            json.dump({'pre_tool': [{'command': 'x'}]}, fh)  # typo
            path = fh.name
        try:
            with patch.dict(os.environ, {'KATO_HOOKS_CONFIG': path}):
                with self.assertRaises(SystemExit):
                    kato_main._load_hooks_or_refuse(app, MagicMock())
        finally:
            os.unlink(path)

    def test_main_calls_load_hooks_before_recover_orphans(self) -> None:
        # Source-inspection guard: the guard runs BEFORE workspace
        # recovery so a missing runner stops boot before side-effects.
        import inspect
        src = inspect.getsource(kato_main.main)
        load_idx = src.index('_load_hooks_or_refuse(app, logger)')
        recover_idx = src.index('_recover_orphan_workspaces(app)')
        self.assertLess(load_idx, recover_idx)


if __name__ == '__main__':
    unittest.main()

"""The default mode for new tasks (Settings → Claude agent).

A task the operator never picked a mode for starts in it — the chat and the
autonomous run alike — while a task they did pick for keeps its pick, and a
``kato:wait-planning`` task still starts in Plan.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_core_lib.data_layers.service.planning_session_runner import _task_mode_spawn
from kato_core_lib.helpers.plan_mode_store import (
    DEFAULT_MODE_CHOICES,
    DEFAULT_MODE_ENV,
    EDIT_AUTOMATICALLY_MODE,
    configured_default_task_mode,
    picked_mode_to_store,
    set_task_mode,
    task_permission_mode,
)


class _Isolated(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = patch.dict(os.environ, {'KATO_PLAN_MODE_PATH': str(Path(tmp.name) / 'plan_mode.json')})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(DEFAULT_MODE_ENV, None)

    def default(self, mode: str):
        return patch.dict(os.environ, {DEFAULT_MODE_ENV: mode})


class ConfiguredDefaultTests(_Isolated):

    def test_unset_is_kato_built_in_default(self) -> None:
        self.assertEqual(configured_default_task_mode(), '')

    def test_every_composer_mode_can_be_the_default(self) -> None:
        for mode in DEFAULT_MODE_CHOICES:
            with self.subTest(mode=mode), self.default(f'  {mode} '):
                self.assertEqual(configured_default_task_mode(), mode)

    def test_an_unknown_value_is_never_handed_to_the_cli(self) -> None:
        for bad in ('yolo', 'acceptEdits', 'PLAN'):
            with self.subTest(bad=bad), self.default(bad):
                self.assertEqual(configured_default_task_mode(), '')


class TaskModeTests(_Isolated):

    def test_a_task_with_no_mode_of_its_own_gets_the_default(self) -> None:
        with self.default('plan'):
            self.assertEqual(task_permission_mode('T-1'), 'plan')
        self.assertEqual(task_permission_mode('T-1'), '')

    def test_a_task_that_was_picked_for_keeps_its_pick(self) -> None:
        set_task_mode('T-1', 'default')
        with self.default('plan'):
            self.assertEqual(task_permission_mode('T-1'), 'default')
            # An explicit caller default still comes before the setting.
            self.assertEqual(task_permission_mode('T-2', 'bypassPermissions'), 'bypassPermissions')
            self.assertEqual(task_permission_mode('  ', 'x'), 'x')

    def test_a_default_reaches_every_spawn_in_full(self) -> None:
        with self.default('plan'):
            self.assertEqual(_task_mode_spawn('T-1')['permission_mode'], 'plan')
        with self.default('explain'):
            spawn = _task_mode_spawn('T-1')
        # Explain is a mode plus a read-only tool set — the default too.
        self.assertEqual(spawn['permission_mode'], 'default')
        self.assertIn('Edit', spawn['disallowed_tools'])
        self.assertEqual(_task_mode_spawn('T-1'), {})


class PickedModeTests(_Isolated):

    def test_edit_automatically_sticks_once_a_default_is_set(self) -> None:
        # As '' it would read back as the default and could never be picked.
        with self.default('plan'):
            self.assertEqual(picked_mode_to_store(''), EDIT_AUTOMATICALLY_MODE)
            set_task_mode('T-1', picked_mode_to_store(''))
            self.assertEqual(task_permission_mode('T-1'), EDIT_AUTOMATICALLY_MODE)
            self.assertEqual(_task_mode_spawn('T-1')['permission_mode'], 'acceptEdits')

    def test_without_a_default_nothing_changes(self) -> None:
        self.assertEqual(picked_mode_to_store(''), '')
        self.assertEqual(picked_mode_to_store(' plan '), 'plan')
        with self.default('plan'):
            self.assertEqual(picked_mode_to_store('explain'), 'explain')


class SettingsTests(unittest.TestCase):

    def test_it_is_a_claude_setting_named_like_the_composer(self) -> None:
        from kato_core_lib.helpers.kato_settings_schema_utils import (
            SETTINGS_SCHEMA,
            validate_settings_values,
        )
        claude = next(section for section in SETTINGS_SCHEMA if section['id'] == 'claude_agent')
        field = next(f for f in claude['fields'] if f[0] == DEFAULT_MODE_ENV)
        extra = field[4]
        self.assertEqual(extra['options'], list(DEFAULT_MODE_CHOICES))
        self.assertEqual(extra['option_labels'], {
            '': 'Edit automatically', 'default': 'Manual', 'explain': 'Explain',
            'plan': 'Plan', 'bypassPermissions': 'Auto',
        })
        self.assertIn('kato:wait-planning', field[3])
        self.assertTrue(validate_settings_values({DEFAULT_MODE_ENV: 'yolo'}))
        self.assertFalse(validate_settings_values({DEFAULT_MODE_ENV: 'plan'}))


if __name__ == '__main__':
    unittest.main()

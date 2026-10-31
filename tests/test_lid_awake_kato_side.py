"""Kato's side of the lid-closed feature: settings, words, wiring, CLI.

The behaviour itself is tested inside ``lid_awake_core_lib``.
"""

from __future__ import annotations

import argparse
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from flask import Flask
from lid_awake_core_lib.lid_awake_core_lib.decision import (
    KEEP_AWAKE_REASONS,
    LidAwakeReason,
)
from lid_awake_core_lib.lid_awake_core_lib.platform_support import (
    UNSUPPORTED,
    PlatformSupport,
)

from kato_core_lib import cli
from kato_core_lib.data_layers.service.lid_awake import (
    identity,
    settings,
    wiring,
)
from kato_core_lib.data_layers.service.lid_awake.agents import agents_are_working
from kato_core_lib.data_layers.service.lid_awake.status_text import (
    LEVEL_ERROR,
    LEVEL_ON,
    status_payload,
)
from kato_webserver.lid_awake_routes import register_lid_awake_routes


def _manager(*sessions):
    records = [SimpleNamespace(task_id=f'T-{i}') for i in range(len(sessions))]
    by_id = {record.task_id: session for record, session in zip(records, sessions)}
    return SimpleNamespace(
        list_records=lambda: records,
        get_session=lambda task_id: by_id[task_id],
    )


def _session(*, working=True, waiting_on=''):
    return SimpleNamespace(
        is_working=working,
        pending_control_request_tool=lambda: waiting_on,
    )


class SettingTests(unittest.TestCase):
    def _enabled(self, stored):
        with mock.patch(
            'kato_core_lib.helpers.kato_settings_store_utils.read_kato_settings',
            return_value=stored,
        ), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(settings.LID_AWAKE_ENABLED_KEY, None)
            return settings.lid_awake_enabled()

    def test_off_unless_the_operator_turns_it_on(self):
        self.assertFalse(self._enabled({}))

    def test_on(self):
        self.assertTrue(self._enabled({settings.LID_AWAKE_ENABLED_KEY: 'true'}))

    def test_a_typo_stays_off(self):
        self.assertFalse(self._enabled({settings.LID_AWAKE_ENABLED_KEY: 'yep'}))


class AgentsTests(unittest.TestCase):
    def test_nobody_home(self):
        self.assertFalse(agents_are_working(_manager()))

    def test_a_working_agent(self):
        self.assertTrue(agents_are_working(_manager(_session())))

    def test_an_idle_agent_does_not_count(self):
        self.assertFalse(agents_are_working(_manager(_session(working=False))))

    def test_an_agent_waiting_for_the_operator_does_not_count(self):
        self.assertFalse(agents_are_working(_manager(_session(waiting_on='Bash'))))

    def test_a_record_with_no_live_session_does_not_count(self):
        self.assertFalse(agents_are_working(_manager(None)))

    def test_any_one_working_agent_is_enough(self):
        self.assertTrue(agents_are_working(_manager(None, _session(waiting_on='Bash'), _session())))


class IdentityTests(unittest.TestCase):
    def test_names(self):
        self.assertEqual(identity.GUARD_IDENTITY.label, 'com.kato.lid-awake-watchdog')
        self.assertEqual(
            identity.GUARD_IDENTITY.sudoers_path, '/etc/sudoers.d/kato-lid-awake',
        )

    def test_heartbeat_lives_with_the_rest_of_katos_state(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(identity.HEARTBEAT_PATH_ENV, None)
            self.assertEqual(identity.heartbeat_path().name, 'lid-awake.heartbeat')
            self.assertEqual(identity.heartbeat_path().parent.name, '.kato')

    def test_heartbeat_path_can_be_moved(self):
        with mock.patch.dict(os.environ, {identity.HEARTBEAT_PATH_ENV: '/tmp/beat'}):
            self.assertEqual(identity.heartbeat_path(), Path('/tmp/beat'))

    def test_the_windows_files_sit_beside_the_heartbeat(self):
        with mock.patch.dict(os.environ, {identity.HEARTBEAT_PATH_ENV: '/tmp/k/beat'}):
            windows = identity.host_identity().windows
        self.assertEqual(windows.script, Path('/tmp/k/lid-awake-watchdog.ps1'))
        self.assertEqual(windows.saved, Path('/tmp/k/lid-awake-lid-action.json'))
        self.assertEqual(windows.run_value, 'KatoLidAwakeWatchdog')

    def test_support_is_built_for_kato(self):
        with mock.patch.object(identity, 'platform_support', return_value=UNSUPPORTED) as build:
            self.assertIs(identity.lid_awake_support(), UNSUPPORTED)
        self.assertEqual(build.call_args.args[0].who, 'kato')


class StatusTextTests(unittest.TestCase):
    def test_off_says_nothing(self):
        self.assertEqual(status_payload({})['label'], '')

    def test_every_other_reason_has_words(self):
        reasons = [
            value for name, value in vars(LidAwakeReason).items()
            if not name.startswith('_') and value != LidAwakeReason.OFF
        ]
        self.assertEqual(len(reasons), 9)
        for reason in reasons:
            payload = status_payload({'reason': reason})
            self.assertTrue(payload['label'], reason)
            self.assertTrue(payload['detail'], reason)

    def test_keep_awake_reasons_read_as_on(self):
        for reason in KEEP_AWAKE_REASONS:
            payload = status_payload({'reason': reason, 'active': True})
            self.assertEqual(payload['level'], LEVEL_ON)
            self.assertTrue(payload['active'])

    def test_a_failure_wins_and_carries_the_reason(self):
        payload = status_payload({
            'reason': LidAwakeReason.ON_CHARGER, 'active': False, 'error': 'pmset failed: no',
        })
        self.assertEqual(payload['level'], LEVEL_ERROR)
        self.assertIn('pmset failed: no', payload['detail'])
        self.assertFalse(payload['active'])

    def test_needs_setup_names_the_command(self):
        detail = status_payload({'reason': LidAwakeReason.NEEDS_SETUP})['detail']
        self.assertIn('kato lid-awake setup', detail)


class WiringTests(unittest.TestCase):
    def _build(self, *, autostart=False, manager=None, support=UNSUPPORTED):
        return wiring.build_and_start_lid_awake_service(
            session_manager=manager or _manager(),
            support=support,
            autostart=autostart,
        )

    def test_uses_katos_sessions_for_agents_working(self):
        service = self._build(manager=_manager(_session()))
        self.assertTrue(service._agents_working())

    def test_uses_katos_heartbeat(self):
        with mock.patch.object(wiring, 'heartbeat_path', return_value=Path('/tmp/x')) as path:
            self._build()
        path.assert_called_once_with()

    def test_autostart(self):
        with mock.patch.object(wiring.LidAwakeService, 'start') as start:
            self._build(autostart=True)
        start.assert_called_once_with()

    def test_defaults_to_this_computer(self):
        with mock.patch.object(wiring, 'lid_awake_support', return_value=UNSUPPORTED) as support:
            wiring.build_and_start_lid_awake_service(session_manager=_manager(), autostart=False)
        support.assert_called_once_with()


class RouteTests(unittest.TestCase):
    def _get(self, service=None):
        app = Flask(__name__)
        if service is not None:
            app.config['LID_AWAKE_SERVICE'] = service
        register_lid_awake_routes(app)
        return app.test_client().get('/api/lid-awake').get_json()

    def test_before_the_service_starts_says_nothing(self):
        self.assertEqual(self._get()['label'], '')

    def test_serves_the_status_in_katos_words(self):
        service = SimpleNamespace(status=lambda: {
            'reason': LidAwakeReason.ON_CHARGER, 'active': True, 'error': '',
        })
        body = self._get(service)
        self.assertEqual(body['label'], 'Lid can close')
        self.assertIs(body['active'], True)


class SettingsSchemaTests(unittest.TestCase):
    def test_saving_it_needs_no_restart(self):
        from kato_webserver.app import _apply_live_settings, _live_settings_keys

        self.assertIn(settings.LID_AWAKE_ENABLED_KEY, _live_settings_keys())
        message = _apply_live_settings(mock.Mock(), {settings.LID_AWAKE_ENABLED_KEY: 'true'})
        self.assertIn('Saved', message)

    def test_it_is_in_settings_with_the_warning(self):
        source = Path(
            'kato_core_lib/helpers/kato_settings_schema_utils.py'
        ).read_text(encoding='utf-8')
        self.assertIn(f"'{settings.LID_AWAKE_ENABLED_KEY}', 'bool'", source)
        self.assertIn('never in a bag', source)

    def test_both_limits_are_in_settings(self):
        source = Path(
            'kato_core_lib/helpers/kato_settings_schema_utils.py'
        ).read_text(encoding='utf-8')
        self.assertIn("'KATO_CLAUDE_MAX_RUN_SECONDS', 'number'", source)


class CliTests(unittest.TestCase):
    def _run(self, action, *, supported=True, ready=True, code=0):
        install, remove = mock.Mock(return_value=code), mock.Mock(return_value=code)
        support = PlatformSupport(
            lid_switch=mock.Mock() if supported else None,
            guard_ready=lambda: ready,
            install_guard=install,
            remove_guard=remove,
        )
        with mock.patch.object(identity, 'lid_awake_support', return_value=support), \
                mock.patch('builtins.print') as printed:
            result = cli.cmd_lid_awake(argparse.Namespace(action=action))
        words = ' '.join(str(call.args[0]) for call in printed.call_args_list)
        return result, words, install, remove

    def test_parser_accepts_it(self):
        args = cli.build_parser().parse_args(['lid-awake', 'setup'])
        self.assertIs(args.func, cli.cmd_lid_awake)
        self.assertEqual(args.action, 'setup')

    def test_works_from_any_folder_on_an_older_install(self):
        with mock.patch.object(cli.sys, 'path', ['/somewhere/else']):
            self._run('status')
            self.assertEqual(cli.sys.path[0], str(cli.REPO_ROOT))

    def test_an_unsupported_computer_says_so(self):
        result, words, install, _ = self._run('setup', supported=False)
        self.assertEqual(result, 1)
        self.assertIn('does not work on this computer', words)
        install.assert_not_called()

    def test_setup_succeeds_only_once_the_guard_is_confirmed(self):
        result, words, install, _ = self._run('setup')
        self.assertEqual(result, 0)
        self.assertIn('Keep running with the lid closed', words)
        install.assert_called_once_with()

    def test_setup_that_ran_but_left_no_guard_is_a_failure(self):
        result, words, _, _ = self._run('setup', ready=False)
        self.assertEqual(result, 1)
        self.assertIn('not active', words)

    def test_setup_passes_on_sudos_failure(self):
        result, words, *_ = self._run('setup', code=1)
        self.assertEqual(result, 1)
        self.assertIn('Setup failed', words)

    def test_remove(self):
        result, words, _, remove = self._run('remove')
        self.assertEqual(result, 0)
        self.assertIn('Normal sleep is back on', words)
        remove.assert_called_once_with()

    def test_a_failed_remove_does_not_claim_success(self):
        result, words, *_ = self._run('remove', code=1)
        self.assertEqual(result, 1)
        self.assertNotIn('back on', words)

    def test_status(self):
        self.assertEqual(self._run('status')[0], 0)
        result, words, *_ = self._run('status', ready=False)
        self.assertEqual(result, 1)
        self.assertIn('not installed', words)


class BootTests(unittest.TestCase):
    def test_start_hands_the_service_to_the_webserver(self):
        from kato_core_lib import main

        flask_app = SimpleNamespace(config={})
        app = SimpleNamespace(
            session_manager=_manager(),
            planning_flask_app=flask_app,
            logger=mock.Mock(),
        )
        with mock.patch(
            'kato_core_lib.data_layers.service.lid_awake.wiring.'
            'build_and_start_lid_awake_service',
            return_value='service',
        ) as build:
            main._start_lid_awake(app)
        build.assert_called_once_with(session_manager=app.session_manager)
        self.assertEqual(app.lid_awake_service, 'service')
        self.assertEqual(flask_app.config['LID_AWAKE_SERVICE'], 'service')

    def test_works_with_no_webserver(self):
        from kato_core_lib import main

        app = SimpleNamespace(session_manager=_manager(), logger=mock.Mock())
        with mock.patch(
            'kato_core_lib.data_layers.service.lid_awake.wiring.'
            'build_and_start_lid_awake_service',
            return_value='service',
        ):
            main._start_lid_awake(app)
        self.assertEqual(app.lid_awake_service, 'service')

    def test_the_auto_resolver_starts_with_the_webserver(self):
        from kato_core_lib import main

        app = SimpleNamespace(planning_flask_app='flask', logger=mock.Mock())
        with mock.patch(
            'kato_webserver.permission_auto_resolver.build_and_start_permission_auto_resolver',
            return_value='resolver',
        ) as build:
            main._start_permission_auto_resolver(app)
        build.assert_called_once_with('flask')
        self.assertEqual(app.permission_auto_resolver, 'resolver')

    def test_the_auto_resolver_needs_a_webserver(self):
        from kato_core_lib import main

        app = SimpleNamespace(logger=mock.Mock())
        main._start_permission_auto_resolver(app)
        self.assertFalse(hasattr(app, 'permission_auto_resolver'))

    def test_a_failing_auto_resolver_never_stops_the_boot(self):
        from kato_core_lib import main

        app = SimpleNamespace(planning_flask_app='flask', logger=mock.Mock())
        with mock.patch(
            'kato_webserver.permission_auto_resolver.build_and_start_permission_auto_resolver',
            side_effect=RuntimeError('boom'),
        ):
            main._start_permission_auto_resolver(app)
        app.logger.exception.assert_called_once()

    def test_a_failure_to_start_never_stops_the_boot(self):
        from kato_core_lib import main

        app = SimpleNamespace(session_manager=None, logger=mock.Mock())
        with mock.patch(
            'kato_core_lib.data_layers.service.lid_awake.wiring.'
            'build_and_start_lid_awake_service',
            side_effect=RuntimeError('boom'),
        ):
            main._start_lid_awake(app)
        app.logger.exception.assert_called_once()


if __name__ == '__main__':
    unittest.main()

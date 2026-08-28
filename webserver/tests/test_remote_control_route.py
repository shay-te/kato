"""``/api/sessions/<task_id>/remote-control`` — the composer's ``/`` menu toggle.

Remote Control hands a live Claude session to claude.ai / the Claude app. It
behaves unlike every other composer setting and the route exists to hold that
difference in one place:

* it is NOT baked at spawn — it goes to the running subprocess as a control
  request, so flipping it takes effect without a respawn;
* it dies with that subprocess, so the operator's choice is persisted and
  re-sent on the next spawn;
* enabling can be REFUSED by the CLI (not signed in, session already remote),
  and a refusal must not leave kato claiming a bridge it never built.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_webserver.app import create_app

_URLS = {
    'session_url': 'https://claude.ai/code/session/abc',
    'connect_url': 'https://claude.ai/code/connect/env-1',
    'bridge_session_id': 'bridge-abc',
}


class _Session:
    def __init__(self, alive=True, error='') -> None:
        self.is_alive = alive
        self._error = error
        self.remote_control = {'enabled': False, 'session_url': '', 'connect_url': ''}
        self.calls: list[tuple[bool, str]] = []

    def set_remote_control(self, enabled, name='', timeout=None):  # noqa: ARG002
        self.calls.append((bool(enabled), name))
        if self._error:
            raise RuntimeError(self._error)
        self.remote_control = (
            {'enabled': True, **_URLS} if enabled
            else {'enabled': False, 'session_url': '', 'connect_url': ''}
        )
        return self.remote_control


class _Manager:
    AGENT_BACKEND = 'claude'

    def __init__(self, session=None, backend='claude') -> None:
        self._session = session
        self._backend = backend

    def list_records(self):
        return []

    def get_record(self, task_id):  # noqa: ARG002
        return None

    def get_session(self, task_id):  # noqa: ARG002
        return self._session

    def backend_for(self, task_id):  # noqa: ARG002
        return self._backend


class _RemoteControlRouteCase(unittest.TestCase):
    supported = True

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Path(self._tmp.name) / 'remote_control.json'
        env = patch.dict(
            os.environ, {'KATO_REMOTE_CONTROL_PATH': str(self.store)},
        )
        env.start()
        self.addCleanup(env.stop)
        probe = patch(
            'claude_core_lib.claude_core_lib.helpers.remote_control.'
            'supports_remote_control',
            return_value=self.supported,
        )
        probe.start()
        self.addCleanup(probe.stop)

    def _app(self, session=None, backend='claude'):
        app = create_app(session_manager=_Manager(session, backend))
        return app, app.test_client()

    def _post(self, client, enabled):
        return client.post(
            '/api/sessions/T1/remote-control', json={'enabled': enabled},
        )


class RemoteControlRouteTests(_RemoteControlRouteCase):
    def test_defaults_to_off_and_unbridged(self) -> None:
        _app, client = self._app()
        body = client.get('/api/sessions/T1/remote-control').get_json()
        self.assertEqual(body['enabled'], False)
        self.assertEqual(body['live'], False)
        self.assertEqual(body['session_url'], '')
        self.assertTrue(body['supported'])

    def test_enabling_a_live_session_returns_the_url(self) -> None:
        session = _Session()
        _app, client = self._app(session)
        body = self._post(client, True).get_json()
        self.assertEqual(body['enabled'], True)
        self.assertEqual(body['live'], True)
        self.assertEqual(body['session_url'], _URLS['session_url'])
        self.assertEqual(session.calls, [(True, 'kato T1')])

    def test_the_session_is_named_after_the_task(self) -> None:
        # The Claude app's session list is all the operator gets to pick from
        # on the other device; the CLI's own default names them all after this
        # machine, which makes them indistinguishable.
        session = _Session()
        _app, client = self._app(session)
        self._post(client, True)
        self.assertEqual(session.calls[0][1], 'kato T1')

    def test_disabling_clears_the_bridge(self) -> None:
        session = _Session()
        _app, client = self._app(session)
        self._post(client, True)
        body = self._post(client, False).get_json()
        self.assertEqual(body['enabled'], False)
        self.assertEqual(body['live'], False)
        self.assertEqual(session.calls[-1][0], False)

    def test_preference_is_persisted_for_the_next_boot(self) -> None:
        _app, client = self._app(_Session())
        self._post(client, True)
        self.assertEqual(json.loads(self.store.read_text()), ['T1'])

    def test_a_persisted_preference_is_reloaded_at_boot(self) -> None:
        self.store.write_text(json.dumps(['T1']))
        _app, client = self._app()
        body = client.get('/api/sessions/T1/remote-control').get_json()
        self.assertTrue(body['enabled'])
        # …but nothing is bridged: there is no subprocess yet.
        self.assertFalse(body['live'])

    def test_no_live_session_still_stores_the_preference(self) -> None:
        # The common case: a tab the operator has not typed in for a while has
        # no subprocess at all. The next message respawns it and applies this.
        _app, client = self._app(session=None)
        body = self._post(client, True).get_json()
        self.assertEqual(body['enabled'], True)
        self.assertEqual(body['live'], False)
        self.assertEqual(body['applied'], False)

    def test_a_dead_session_is_not_toggled(self) -> None:
        session = _Session(alive=False)
        _app, client = self._app(session)
        self._post(client, True)
        self.assertEqual(session.calls, [])

    def test_a_dead_session_reports_unbridged(self) -> None:
        session = _Session(alive=False)
        session.remote_control = {'enabled': False, 'session_url': '', 'connect_url': ''}
        _app, client = self._app(session)
        body = client.get('/api/sessions/T1/remote-control').get_json()
        self.assertFalse(body['live'])


class RemoteControlRefusalTests(_RemoteControlRouteCase):
    def test_a_refused_enable_is_not_persisted(self) -> None:
        # Otherwise the toggle would read "on" forever while nothing was ever
        # bridged, and every respawn would retry a call the CLI rejects.
        session = _Session(error='Remote Control cannot be enabled')
        _app, client = self._app(session)
        response = self._post(client, True)
        self.assertEqual(response.status_code, 502)
        self.assertIn('cannot be enabled', response.get_json()['error'])
        self.assertFalse(response.get_json()['enabled'])
        self.assertFalse(self.store.exists())

    def test_a_failed_disable_is_still_persisted_off(self) -> None:
        # "Stop exposing this session" is not a request kato gets to defer: a
        # preference left on would silently re-bridge on the next spawn.
        self.store.write_text(json.dumps(['T1']))
        session = _Session(error='bridge already gone')
        _app, client = self._app(session)
        response = self._post(client, False)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(json.loads(self.store.read_text()), [])


class RemoteControlSupportGateTests(_RemoteControlRouteCase):
    def test_a_codex_task_is_not_supported(self) -> None:
        _app, client = self._app(backend='codex')
        self.assertFalse(
            client.get('/api/sessions/T1/remote-control').get_json()['supported'],
        )

    def test_enabling_an_unsupported_backend_is_refused(self) -> None:
        _app, client = self._app(_Session(), backend='codex')
        response = self._post(client, True)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.store.exists())


class RemoteControlBinaryTests(_RemoteControlRouteCase):
    """WHICH CLI gets probed for support.

    The planning runner holds one backend's binary. On a multi-backend host
    that can be Codex's — and probing ``codex --help`` for ``--remote-control``
    reported the feature unsupported on the Claude tab, which is the only tab
    that can use it.
    """

    def _probe_binary(self, app) -> str:
        with patch(
            'claude_core_lib.claude_core_lib.helpers.remote_control.'
            'supports_remote_control',
            return_value=True,
        ) as probe:
            app.test_client().get('/api/sessions/T1/remote-control')
        return probe.call_args.args[0]

    def test_uses_the_configured_claude_binary(self) -> None:
        app, _client = self._app()
        app.config['AGENT_BINARIES'] = {'claude': '/opt/bin/claude', 'codex': 'codex'}
        self.assertEqual(self._probe_binary(app), '/opt/bin/claude')

    def test_never_probes_another_backends_binary(self) -> None:
        app, _client = self._app()
        app.config['AGENT_BINARIES'] = {}
        app.config['PLANNING_SESSION_RUNNER'] = type(
            'R', (), {'_defaults': type('D', (), {'binary': 'codex'})()},
        )()
        self.assertEqual(self._probe_binary(app), 'claude')

    def test_falls_back_to_the_bare_name(self) -> None:
        app, _client = self._app()
        app.config['AGENT_BINARIES'] = {}
        self.assertEqual(self._probe_binary(app), 'claude')


class RemoteControlOldCliTests(_RemoteControlRouteCase):
    supported = False

    def test_an_old_cli_reports_unsupported(self) -> None:
        _app, client = self._app()
        self.assertFalse(
            client.get('/api/sessions/T1/remote-control').get_json()['supported'],
        )

    def test_enabling_on_an_old_cli_is_refused(self) -> None:
        response = self._post(self._app(_Session())[1], True)
        self.assertEqual(response.status_code, 400)


if __name__ == '__main__':
    unittest.main()

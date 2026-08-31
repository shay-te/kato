"""Both agent tabs always exist; ``ready`` decides chat vs. setup panel.

Codex used to be hidden behind an env-only flag, so an operator with the
CLI installed had no way to discover kato could use it. Now both backends
always come back from ``/api/agent-backends`` and an unready one opens a
setup panel carrying the transport's OWN install/login text.
"""

from __future__ import annotations

import unittest
import unittest.mock
from unittest.mock import patch

from kato_core_lib.helpers import agent_backend_readiness as readiness


class ProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        readiness.reset_probe_cache()
        self.addCleanup(readiness.reset_probe_cache)

    def test_a_working_cli_reports_ready(self) -> None:
        with patch.object(readiness, '_build_probe_client') as build:
            build.return_value.validate_connection.return_value = None
            result = readiness.probe_backend('codex')
        self.assertTrue(result['ready'])
        self.assertEqual(result['error'], '')
        self.assertEqual(result['id'], 'codex')
        self.assertEqual(result['label'], 'Codex')

    def test_a_missing_cli_reports_the_validator_message(self) -> None:
        message = (
            'Codex CLI ("codex") was not found on PATH.\n\n'
            '    npm install -g @openai/codex\n'
        )
        with patch.object(readiness, '_build_probe_client') as build:
            build.return_value.validate_connection.side_effect = RuntimeError(message)
            result = readiness.probe_backend('codex')
        self.assertFalse(result['ready'])
        # Passed through verbatim — the panel shows the same words the boot
        # validator would, not a second copy that drifts.
        self.assertIn('npm install -g @openai/codex', result['error'])

    def test_an_unexpected_failure_is_reported_not_raised(self) -> None:
        with patch.object(readiness, '_build_probe_client') as build:
            build.side_effect = ImportError('no module')
            result = readiness.probe_backend('codex')
        self.assertFalse(result['ready'])
        self.assertIn('no module', result['error'])

    def test_an_exception_with_no_message_still_yields_an_error(self) -> None:
        with patch.object(readiness, '_build_probe_client') as build:
            build.return_value.validate_connection.side_effect = RuntimeError('')
            result = readiness.probe_backend('codex')
        self.assertEqual(result['error'], 'RuntimeError')

    def test_an_unknown_backend_is_not_ready(self) -> None:
        result = readiness.probe_backend('nope')
        self.assertFalse(result['ready'])
        self.assertIn('unknown agent backend', result['error'])

    def test_the_result_is_cached(self) -> None:
        with patch.object(readiness, '_build_probe_client') as build:
            build.return_value.validate_connection.return_value = None
            readiness.probe_backend('codex')
            readiness.probe_backend('codex')
            self.assertEqual(build.call_count, 1)

    def test_the_cache_expires(self) -> None:
        clock = iter([0.0, readiness.PROBE_CACHE_SECONDS + 1])
        with patch.object(readiness, '_build_probe_client') as build:
            build.return_value.validate_connection.return_value = None
            readiness.probe_backend('codex', now=lambda: next(clock))
            readiness.probe_backend('codex', now=lambda: next(clock))
            self.assertEqual(build.call_count, 2)

    def test_reset_forces_a_re_probe(self) -> None:
        with patch.object(readiness, '_build_probe_client') as build:
            build.return_value.validate_connection.return_value = None
            readiness.probe_backend('codex')
            readiness.reset_probe_cache()
            readiness.probe_backend('codex')
            self.assertEqual(build.call_count, 2)

    def test_both_chat_backends_are_always_listed(self) -> None:
        with patch.object(readiness, '_build_probe_client') as build:
            build.return_value.validate_connection.side_effect = RuntimeError('nope')
            entries = readiness.probe_chat_backends()
        # Not-ready must NOT drop the entry — that is the whole point: a
        # hidden tab is how the backend stays undiscovered.
        self.assertEqual([e['id'] for e in entries], ['claude', 'codex'])
        self.assertTrue(all(e['ready'] is False for e in entries))

    def test_a_configured_binary_path_is_used(self) -> None:
        with patch.object(readiness, '_build_probe_client') as build:
            build.return_value.validate_connection.return_value = None
            readiness.probe_chat_backends({'codex': '/opt/bin/codex'})
        binaries = {
            (c.kwargs.get('binary') if 'binary' in c.kwargs else c.args[1])
            for c in build.call_args_list
        }
        self.assertIn('/opt/bin/codex', binaries)


class ProbeClientTests(unittest.TestCase):
    """The probe builds a REAL transport client, wired with just the binary."""

    def test_codex_probe_client_is_the_codex_transport(self) -> None:
        client = readiness._build_probe_client('codex', '')
        self.assertEqual(type(client).__name__, 'CodexCliClient')

    def test_claude_probe_client_is_the_claude_transport(self) -> None:
        client = readiness._build_probe_client('claude', '')
        self.assertEqual(type(client).__name__, 'ClaudeCliClient')

    def test_an_unknown_backend_builds_nothing(self) -> None:
        self.assertIsNone(readiness._build_probe_client('openhands', ''))


class BackendsEndpointTests(unittest.TestCase):
    """``/api/agent-backends`` is what draws the chat tabs."""

    def setUp(self) -> None:
        import tempfile
        from kato_webserver.app import create_app, _build_fallback_manager
        readiness.reset_probe_cache()
        self.addCleanup(readiness.reset_probe_cache)
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-backends-')
        self.addCleanup(self._tmp.cleanup)
        app = create_app(
            session_manager=_build_fallback_manager(self._tmp.name),
            fallback_state_dir=self._tmp.name,
        )
        self.client = app.test_client()

    def _get(self, path='/api/agent-backends'):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_both_chat_backends_are_returned(self) -> None:
        with patch.object(readiness, '_build_probe_client') as build:
            build.return_value.validate_connection.return_value = None
            body = self._get()
        self.assertEqual([e['id'] for e in body['backends']], ['claude', 'codex'])

    def test_each_entry_carries_what_the_ui_needs(self) -> None:
        with patch.object(readiness, '_build_probe_client') as build:
            build.return_value.validate_connection.return_value = None
            body = self._get()
        for entry in body['backends']:
            self.assertEqual(
                set(entry),
                {
                    'id', 'label', 'ready', 'error', 'wired',
                    'chat_available',
                    # Whether this backend keeps conversations on THIS
                    # machine, so the UI can hide the adopt control for one
                    # that does not (OpenHands runs sessions server-side, so
                    # its picker could only ever come back empty).
                    'supports_session_adoption',
                },
            )

    def test_an_unready_backend_is_listed_not_dropped(self) -> None:
        def probe(backend, binary):
            client = unittest.mock.Mock()
            if backend == 'codex':
                client.validate_connection.side_effect = RuntimeError('no codex')
            return client
        with patch.object(readiness, '_build_probe_client', probe):
            body = self._get()
        codex = next(e for e in body['backends'] if e['id'] == 'codex')
        self.assertFalse(codex['ready'])
        self.assertFalse(codex['chat_available'])
        self.assertIn('no codex', codex['error'])

    def test_wired_and_ready_are_separate_questions(self) -> None:
        """A ready CLI with no session manager still cannot chat."""
        with patch.object(readiness, '_build_probe_client') as build:
            build.return_value.validate_connection.return_value = None
            body = self._get()
        codex = next(e for e in body['backends'] if e['id'] == 'codex')
        self.assertTrue(codex['ready'])
        # The fallback manager wires claude only, so codex is ready-but-unwired
        # and chat_available must reflect BOTH.
        self.assertEqual(codex['chat_available'], codex['wired'])

    def test_refresh_forces_a_re_probe(self) -> None:
        # Clearing the probe cache is a POST now. It is process-GLOBAL — one
        # client's refresh invalidates it for every other client — and browsers
        # issue GETs nobody asked for, so it had no business on a read. See
        # webserver/kato_webserver/cache_refresh.py.
        with patch.object(readiness, '_build_probe_client') as build:
            build.return_value.validate_connection.return_value = None
            self._get()
            first = build.call_count
            self._get()
            self.assertEqual(build.call_count, first, 'second call was not cached')

            self.client.post('/api/refresh', json={'target': 'agent-backends'})
            self._get()
            self.assertGreater(build.call_count, first)

    def test_the_GET_no_longer_forces_a_re_probe(self) -> None:
        with patch.object(readiness, '_build_probe_client') as build:
            build.return_value.validate_connection.return_value = None
            self._get()
            first = build.call_count
            self._get('/api/agent-backends?refresh=1')
            self.assertEqual(build.call_count, first, 'the GET still re-probed')


if __name__ == '__main__':
    unittest.main()

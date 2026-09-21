"""The planning-UI build is best effort; the committed bundle is the floor.

Reported from Pop!_OS: bootstrap died at ``npm run build`` with
``crypto$2.getRandomValues is not a function`` — Vite reaching for a global
``crypto`` that old Node does not expose.

The defect is not the Node version, it is that this step was FATAL. npm absent
skipped politely and used the committed bundle, while npm present-but-unusable
killed the whole bootstrap — the harder failure got the harsher treatment, and
an operator who only wanted to RUN kato was blocked by a build they did not
need.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_bootstrap():
    sys.path.insert(0, str(REPO_ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location(
        'kato_bootstrap_ui_under_test', REPO_ROOT / 'scripts' / 'bootstrap.py',
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CommittedBundleIsTheFallbackTests(unittest.TestCase):
    """The fallback only works if the bundle is really on disk and tracked."""

    def test_the_bundle_exists_and_is_committed(self) -> None:
        bundle = REPO_ROOT / 'webserver' / 'static' / 'build' / 'app.js'
        self.assertTrue(bundle.is_file(), 'no committed bundle to fall back to')
        tracked = subprocess.run(
            ['git', 'ls-files', '--error-unmatch', str(bundle)],
            cwd=REPO_ROOT, capture_output=True,
        )
        self.assertEqual(
            tracked.returncode, 0,
            'app.js is not tracked — a fresh clone would have no UI at all',
        )

    def test_the_stylesheet_exists_too(self) -> None:
        # app.css is built by the sass half of ``npm run build``; a skipped
        # build must still leave a styled UI.
        self.assertTrue(
            (REPO_ROOT / 'webserver' / 'static' / 'css' / 'app.css').is_file(),
        )


class NodeCapabilityProbeTests(unittest.TestCase):

    def setUp(self) -> None:
        self.bootstrap = _load_bootstrap()

    def test_this_machines_node_can_build(self) -> None:
        # Positive control: without it, the negative cases below could pass
        # because the probe always says no.
        if not self.bootstrap.have_executable('node'):
            self.skipTest('node is not installed here')
        ok, reason = self.bootstrap._node_can_build()
        self.assertTrue(ok, f'probe rejected a working node: {reason}')

    def test_it_probes_the_capability_rather_than_parsing_a_version(self) -> None:
        # The boundary release for the global crypto has moved around, so
        # asking Node whether it HAS the thing cannot be wrong about it.
        source = (REPO_ROOT / 'scripts' / 'bootstrap.py').read_text()
        self.assertIn('globalThis.crypto', source)
        self.assertIn('getRandomValues', source)

    def test_an_absent_node_is_reported_not_raised(self) -> None:
        with mock.patch.object(
            self.bootstrap.subprocess, 'run', side_effect=OSError('no node'),
        ):
            ok, reason = self.bootstrap._node_can_build()
        self.assertFalse(ok)
        self.assertIn('not runnable', reason)

    def test_a_node_without_the_global_names_the_real_error(self) -> None:
        calls = [
            mock.Mock(stdout='v12.22.9\n', stderr=''),   # node --version
            mock.Mock(returncode=1),                     # capability probe
        ]
        with mock.patch.object(self.bootstrap.subprocess, 'run', side_effect=calls):
            ok, reason = self.bootstrap._node_can_build()
        self.assertFalse(ok)
        self.assertIn('v12.22.9', reason)
        # The operator pasted this string; it must be findable in our output.
        self.assertIn('getRandomValues', reason)
        self.assertIn('Node 20', reason)


class BuildFailureDoesNotKillBootstrapTests(unittest.TestCase):
    """The behaviour change itself."""

    def setUp(self) -> None:
        self.bootstrap = _load_bootstrap()

    def test_a_failing_npm_build_warns_and_returns(self) -> None:
        with mock.patch.object(self.bootstrap, 'have_executable', return_value=True), \
             mock.patch.object(
                 self.bootstrap, '_node_can_build', return_value=(True, 'v20.0.0'),
             ), \
             mock.patch.object(
                 self.bootstrap.subprocess, 'run',
                 return_value=mock.Mock(returncode=1),
             ), \
             mock.patch('sys.stderr') as stderr:
            # Must NOT raise SystemExit.
            self.bootstrap._maybe_build_ui_bundle()

        printed = ''.join(
            str(call.args[0]) for call in stderr.write.call_args_list if call.args
        )
        self.assertIn('WARNING', printed)
        self.assertIn('committed bundle', printed)
        self.assertIn('npm run build', printed)

    def test_an_unusable_node_skips_before_running_npm(self) -> None:
        with mock.patch.object(self.bootstrap, 'have_executable', return_value=True), \
             mock.patch.object(
                 self.bootstrap, '_node_can_build',
                 return_value=(False, 'node v12 has no global crypto'),
             ), \
             mock.patch.object(self.bootstrap.subprocess, 'run') as ran:
            self.bootstrap._maybe_build_ui_bundle()

        ran.assert_not_called()

    def test_the_ui_declares_the_node_it_needs(self) -> None:
        # So npm itself warns before Vite crashes with a stack trace that
        # names neither Node nor a version.
        manifest = json.loads(
            (REPO_ROOT / 'webserver' / 'ui' / 'package.json').read_text(),
        )
        self.assertIn('node', manifest.get('engines', {}))


if __name__ == '__main__':
    unittest.main()

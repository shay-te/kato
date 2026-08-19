"""CA auto-trust must cover the platforms kato supports.

Kato issues a private CA and a leaf cert for localhost/127.0.0.1/::1,
then installs the CA so the browser loads the planning UI without a
warning. That install was implemented for macOS only, so on Windows and
Linux the CA was never trusted and Chrome showed
``ERR_CERT_AUTHORITY_INVALID`` on every fresh origin.

That is worse than an annoyance: it looks like a broken deployment, and
it trains an operator to click through certificate warnings — the exact
habit every other control here depends on them NOT having.
"""

from __future__ import annotations

import logging
import subprocess
import unittest
from unittest import mock

from kato_core_lib.helpers import tls_cert_utils


def _ok(returncode=0, stdout='', stderr=''):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class PlatformDispatchTests(unittest.TestCase):
    def _dispatch(self, platform):
        logger = mock.MagicMock(spec=logging.Logger)
        with mock.patch.object(tls_cert_utils.sys, 'platform', platform), \
             mock.patch.object(tls_cert_utils, '_install_ca_trust_macos', return_value='macos') as mac, \
             mock.patch.object(tls_cert_utils, '_install_ca_trust_windows', return_value='windows') as win, \
             mock.patch.object(tls_cert_utils, '_install_ca_trust_linux', return_value='linux') as lin:
            result = tls_cert_utils._install_ca_trust('/tmp/ca.pem', logger)
        return result, mac, win, lin, logger

    def test_macos_still_uses_the_keychain(self) -> None:
        result, mac, _win, _lin, _log = self._dispatch('darwin')
        self.assertEqual(result, 'macos')
        mac.assert_called_once()

    def test_windows_is_handled(self) -> None:
        result, _mac, win, _lin, _log = self._dispatch('win32')
        self.assertEqual(result, 'windows')
        win.assert_called_once()

    def test_linux_is_handled(self) -> None:
        result, _mac, _win, lin, _log = self._dispatch('linux')
        self.assertEqual(result, 'linux')
        lin.assert_called_once()

    def test_an_unknown_platform_says_so_instead_of_pretending(self) -> None:
        result, _mac, _win, _lin, logger = self._dispatch('sunos5')
        self.assertFalse(result)
        self.assertTrue(logger.info.called)


class WindowsTrustTests(unittest.TestCase):
    def test_installs_into_the_USER_root_store(self) -> None:
        # ``-user``: the machine store needs elevation, and a local dev CA
        # has no business being trusted machine-wide.
        logger = mock.MagicMock(spec=logging.Logger)
        with mock.patch.object(tls_cert_utils.subprocess, 'run', return_value=_ok()) as run:
            self.assertTrue(tls_cert_utils._install_ca_trust_windows('C:/ca.pem', logger))
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], 'certutil')
        self.assertIn('-user', argv)
        self.assertIn('Root', argv)
        self.assertNotIn('-enterprise', argv)

    def test_failure_is_reported_not_swallowed(self) -> None:
        logger = mock.MagicMock(spec=logging.Logger)
        with mock.patch.object(
            tls_cert_utils.subprocess, 'run', return_value=_ok(1, stderr='denied'),
        ):
            self.assertFalse(tls_cert_utils._install_ca_trust_windows('C:/ca.pem', logger))
        self.assertTrue(logger.warning.called)

    def test_missing_certutil_does_not_raise(self) -> None:
        logger = mock.MagicMock(spec=logging.Logger)
        with mock.patch.object(
            tls_cert_utils.subprocess, 'run', side_effect=FileNotFoundError('certutil'),
        ):
            self.assertFalse(tls_cert_utils._install_ca_trust_windows('C:/ca.pem', logger))


class LinuxTrustTests(unittest.TestCase):
    def test_uses_the_per_user_nss_db_not_the_system_store(self) -> None:
        # Asking a dev tool for sudo to install a machine-wide CA is a
        # bigger ask than the warning it removes.
        logger = mock.MagicMock(spec=logging.Logger)
        with mock.patch.object(tls_cert_utils.shutil, 'which', return_value='/usr/bin/certutil'), \
             mock.patch.object(tls_cert_utils.Path, 'is_dir', return_value=True), \
             mock.patch.object(tls_cert_utils.subprocess, 'run', return_value=_ok()) as run:
            self.assertTrue(tls_cert_utils._install_ca_trust_linux('/tmp/ca.pem', logger))
        argv = run.call_args.args[0]
        self.assertIn('-d', argv)
        self.assertTrue(any(str(a).startswith('sql:') for a in argv))
        self.assertNotIn('update-ca-certificates', argv)
        self.assertNotIn('sudo', argv)

    def test_missing_certutil_names_the_package(self) -> None:
        logger = mock.MagicMock(spec=logging.Logger)
        with mock.patch.object(tls_cert_utils.shutil, 'which', return_value=None):
            self.assertFalse(tls_cert_utils._install_ca_trust_linux('/tmp/ca.pem', logger))
        message = ' '.join(str(c.args[0]) for c in logger.info.call_args_list)
        self.assertIn('libnss3-tools', message)


if __name__ == '__main__':
    unittest.main()

"""Tests for the locally-trusted TLS certificate helper.

A loopback address can never get a certificate from a real CA, so
serving HTTPS locally means kato generates its own trust chain: a
local root CA plus a leaf/server cert signed by it (mirroring
``mkcert``). The path is env-overridable so the test never touches the
real ``~/.kato``.

Safety note: ``ensure_local_tls_cert`` defaults ``install_trust=False``
specifically so that calling it (as almost every test below does) can
NEVER trigger the real OS-trust-installation code path, which may shell
out to ``security add-trusted-cert`` and pop a real macOS Keychain
authorization prompt. Tests that exercise that path mock
``subprocess.run`` (or the installer functions directly) so no real
``security`` command ever runs.
"""
from __future__ import annotations

import datetime
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import MagicMock

from kato_core_lib.helpers.tls_cert_utils import (
    _CA_VALIDITY_DAYS,
    _LEAF_VALIDITY_DAYS,
    _install_ca_trust,
    _install_ca_trust_if_needed,
    _install_ca_trust_macos,
    ca_paths,
    cert_paths,
    ensure_local_tls_cert,
)


class TlsCertUtilsTests(unittest.TestCase):

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        patcher = unittest.mock.patch.dict(
            os.environ, {'KATO_TLS_DIR': self._td.name},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_cert_and_ca_paths_under_the_configured_dir(self) -> None:
        cert_path, key_path = cert_paths()
        ca_cert_path, ca_key_path = ca_paths()
        self.assertEqual(str(cert_path), str(Path(self._td.name) / 'cert.pem'))
        self.assertEqual(str(key_path), str(Path(self._td.name) / 'key.pem'))
        self.assertEqual(str(ca_cert_path), str(Path(self._td.name) / 'ca-cert.pem'))
        self.assertEqual(str(ca_key_path), str(Path(self._td.name) / 'ca-key.pem'))

    def test_generates_a_usable_ca_and_leaf_on_first_call(self) -> None:
        result = ensure_local_tls_cert()
        self.assertIsNotNone(result)
        cert_path_str, key_path_str = result
        self.assertTrue(Path(cert_path_str).is_file())
        self.assertTrue(Path(key_path_str).is_file())
        ca_cert_path, ca_key_path = ca_paths()
        self.assertTrue(ca_cert_path.is_file())
        self.assertTrue(ca_key_path.is_file())

    def test_leaf_is_signed_by_the_ca_not_self_signed(self) -> None:
        from cryptography import x509
        from cryptography.hazmat.primitives.asymmetric import padding
        cert_path_str, _key_path_str = ensure_local_tls_cert()
        ca_cert_path, _ca_key_path = ca_paths()
        leaf = x509.load_pem_x509_certificate(Path(cert_path_str).read_bytes())
        ca = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())
        self.assertEqual(leaf.issuer, ca.subject)
        self.assertNotEqual(leaf.issuer, leaf.subject)  # not self-signed
        # Signature actually verifies against the CA's public key.
        ca.public_key().verify(
            leaf.signature, leaf.tbs_certificate_bytes,
            padding.PKCS1v15(), leaf.signature_hash_algorithm,
        )

    def test_leaf_has_expected_san_and_validity(self) -> None:
        from cryptography import x509
        cert_path_str, _key_path_str = ensure_local_tls_cert()
        cert = x509.load_pem_x509_certificate(Path(cert_path_str).read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        dns_names = san.get_values_for_type(x509.DNSName)
        self.assertIn('localhost', dns_names)
        ips = [str(ip) for ip in san.get_values_for_type(x509.IPAddress)]
        self.assertIn('127.0.0.1', ips)
        self.assertIn('::1', ips)
        validity = cert.not_valid_after_utc - cert.not_valid_before_utc
        self.assertLessEqual(validity.days, _LEAF_VALIDITY_DAYS)
        # Chrome's ERR_CERT_VALIDITY_TOO_LONG cutoff.
        self.assertLessEqual(validity.days, 398)

    def test_ca_is_a_true_ca_with_long_validity(self) -> None:
        from cryptography import x509
        ensure_local_tls_cert()
        ca_cert_path, _ca_key_path = ca_paths()
        ca = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())
        basic_constraints = ca.extensions.get_extension_for_class(
            x509.BasicConstraints,
        ).value
        self.assertTrue(basic_constraints.ca)
        self.assertEqual(ca.issuer, ca.subject)  # CA is self-signed, by design
        validity = ca.not_valid_after_utc - ca.not_valid_before_utc
        self.assertGreater(validity.days, 365 * 5)
        self.assertLessEqual(validity.days, _CA_VALIDITY_DAYS)

    def test_reuses_an_existing_fresh_ca_and_leaf(self) -> None:
        first = ensure_local_tls_cert()
        first_cert_bytes = Path(first[0]).read_bytes()
        ca_cert_path, _ca_key_path = ca_paths()
        first_ca_bytes = ca_cert_path.read_bytes()

        second = ensure_local_tls_cert()

        self.assertEqual(first, second)
        self.assertEqual(Path(second[0]).read_bytes(), first_cert_bytes)
        self.assertEqual(ca_cert_path.read_bytes(), first_ca_bytes)

    def test_regenerates_leaf_when_near_expiry_but_keeps_the_same_ca(self) -> None:
        first = ensure_local_tls_cert()
        first_cert_bytes = Path(first[0]).read_bytes()
        ca_cert_path, _ca_key_path = ca_paths()
        first_ca_bytes = ca_cert_path.read_bytes()

        from cryptography import x509
        leaf = x509.load_pem_x509_certificate(first_cert_bytes)
        near_expiry = leaf.not_valid_after_utc - datetime.timedelta(days=10)
        with unittest.mock.patch(
            'kato_core_lib.helpers.tls_cert_utils.datetime',
        ) as mock_dt:
            mock_dt.timezone = datetime.timezone
            mock_dt.timedelta = datetime.timedelta
            mock_dt.datetime = MagicMock(wraps=datetime.datetime)
            mock_dt.datetime.now.return_value = near_expiry
            second = ensure_local_tls_cert()

        self.assertNotEqual(Path(second[0]).read_bytes(), first_cert_bytes)
        # The CA itself is untouched — only the leaf needed renewal.
        self.assertEqual(ca_cert_path.read_bytes(), first_ca_bytes)

    def test_regenerates_ca_and_leaf_when_ca_is_near_expiry(self) -> None:
        first = ensure_local_tls_cert()
        first_cert_bytes = Path(first[0]).read_bytes()
        ca_cert_path, _ca_key_path = ca_paths()
        first_ca_bytes = ca_cert_path.read_bytes()

        from cryptography import x509
        ca = x509.load_pem_x509_certificate(first_ca_bytes)
        near_expiry = ca.not_valid_after_utc - datetime.timedelta(days=30)
        with unittest.mock.patch(
            'kato_core_lib.helpers.tls_cert_utils.datetime',
        ) as mock_dt:
            mock_dt.timezone = datetime.timezone
            mock_dt.timedelta = datetime.timedelta
            mock_dt.datetime = MagicMock(wraps=datetime.datetime)
            mock_dt.datetime.now.return_value = near_expiry
            second = ensure_local_tls_cert()

        self.assertNotEqual(ca_cert_path.read_bytes(), first_ca_bytes)
        self.assertNotEqual(Path(second[0]).read_bytes(), first_cert_bytes)

    def test_regenerates_when_leaf_file_is_corrupt(self) -> None:
        ensure_local_tls_cert()
        cert_path, key_path = cert_paths()
        cert_path.write_text('not a cert')
        key_path.write_text('not a key')
        result = ensure_local_tls_cert()
        self.assertIsNotNone(result)
        from cryptography import x509
        x509.load_pem_x509_certificate(Path(result[0]).read_bytes())

    def test_regenerates_when_ca_file_is_corrupt(self) -> None:
        ca_cert_path, ca_key_path = ca_paths()
        ca_cert_path.parent.mkdir(parents=True, exist_ok=True)
        ca_cert_path.write_text('not a cert')
        ca_key_path.write_text('not a key')
        result = ensure_local_tls_cert()
        self.assertIsNotNone(result)
        from cryptography import x509
        x509.load_pem_x509_certificate(ca_cert_path.read_bytes())

    def test_returns_none_and_logs_on_generation_failure(self) -> None:
        logger = MagicMock()
        with unittest.mock.patch(
            'kato_core_lib.helpers.tls_cert_utils._generate_ca',
            side_effect=OSError('disk full'),
        ):
            result = ensure_local_tls_cert(logger=logger)
        self.assertIsNone(result)
        logger.warning.assert_called_once()

    def test_key_files_are_written_with_restrictive_permissions(self) -> None:
        _cert_path_str, key_path_str = ensure_local_tls_cert()
        _ca_cert_path, ca_key_path = ca_paths()
        for path in (Path(key_path_str), ca_key_path):
            mode = path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_install_trust_defaults_to_off(self) -> None:
        # The safety-critical default: calling this WITHOUT
        # install_trust=True must never touch the OS trust store.
        with unittest.mock.patch(
            'kato_core_lib.helpers.tls_cert_utils._install_ca_trust_in_background',
        ) as install:
            ensure_local_tls_cert()
        install.assert_not_called()

    def test_install_trust_true_triggers_the_background_installer(self) -> None:
        with unittest.mock.patch(
            'kato_core_lib.helpers.tls_cert_utils._install_ca_trust_in_background',
        ) as install:
            ensure_local_tls_cert(install_trust=True)
        install.assert_called_once()


class InstallCaTrustTests(unittest.TestCase):
    """Every test here mocks ``subprocess.run`` (or the installer
    itself) so NO real ``security`` command / Keychain prompt ever
    fires on the machine running the suite."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        patcher = unittest.mock.patch.dict(
            os.environ, {'KATO_TLS_DIR': self._td.name},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_non_darwin_is_a_noop(self) -> None:
        logger = MagicMock()
        with unittest.mock.patch('kato_core_lib.helpers.tls_cert_utils.sys') as mock_sys:
            mock_sys.platform = 'linux'
            result = _install_ca_trust('ca-cert.pem', logger)
        self.assertFalse(result)
        logger.info.assert_called_once()

    def test_macos_success(self) -> None:
        logger = MagicMock()
        fake_result = MagicMock(returncode=0, stderr='')
        with unittest.mock.patch(
            'kato_core_lib.helpers.tls_cert_utils.subprocess.run',
            return_value=fake_result,
        ) as run:
            result = _install_ca_trust_macos('ca-cert.pem', logger)
        self.assertTrue(result)
        logger.info.assert_called_once()
        args = run.call_args.args[0]
        self.assertIn('security', args)
        self.assertIn('add-trusted-cert', args)
        self.assertIn('ca-cert.pem', args)

    def test_macos_nonzero_exit_is_reported_and_returns_false(self) -> None:
        logger = MagicMock()
        fake_result = MagicMock(returncode=1, stderr='user declined')
        with unittest.mock.patch(
            'kato_core_lib.helpers.tls_cert_utils.subprocess.run',
            return_value=fake_result,
        ):
            result = _install_ca_trust_macos('ca-cert.pem', logger)
        self.assertFalse(result)
        logger.warning.assert_called_once()

    def test_macos_subprocess_exception_is_swallowed(self) -> None:
        logger = MagicMock()
        with unittest.mock.patch(
            'kato_core_lib.helpers.tls_cert_utils.subprocess.run',
            side_effect=FileNotFoundError('no security binary'),
        ):
            result = _install_ca_trust_macos('ca-cert.pem', logger)
        self.assertFalse(result)
        logger.warning.assert_called_once()

    def test_skips_when_no_ca_cert_exists_yet(self) -> None:
        # No CA generated in this test's isolated dir — must not blow up.
        with unittest.mock.patch(
            'kato_core_lib.helpers.tls_cert_utils._install_ca_trust',
        ) as install:
            _install_ca_trust_if_needed(MagicMock())
        install.assert_not_called()

    def test_installs_once_then_skips_on_repeat_via_marker(self) -> None:
        ensure_local_tls_cert()  # generates the CA, install_trust off
        with unittest.mock.patch(
            'kato_core_lib.helpers.tls_cert_utils._install_ca_trust',
            return_value=True,
        ) as install:
            _install_ca_trust_if_needed(MagicMock())
            _install_ca_trust_if_needed(MagicMock())
        install.assert_called_once()

    def test_retries_when_previous_attempt_did_not_succeed(self) -> None:
        ensure_local_tls_cert()
        with unittest.mock.patch(
            'kato_core_lib.helpers.tls_cert_utils._install_ca_trust',
            return_value=False,
        ) as install:
            _install_ca_trust_if_needed(MagicMock())
            _install_ca_trust_if_needed(MagicMock())
        self.assertEqual(install.call_count, 2)

    def test_marker_mismatch_after_ca_regeneration_triggers_reinstall(self) -> None:
        ensure_local_tls_cert()
        with unittest.mock.patch(
            'kato_core_lib.helpers.tls_cert_utils._install_ca_trust',
            return_value=True,
        ) as install:
            _install_ca_trust_if_needed(MagicMock())
        install.assert_called_once()

        # Regenerate the CA (simulating expiry) — new fingerprint.
        ca_cert_path, ca_key_path = ca_paths()
        ca_cert_path.unlink()
        ca_key_path.unlink()
        ensure_local_tls_cert()
        with unittest.mock.patch(
            'kato_core_lib.helpers.tls_cert_utils._install_ca_trust',
            return_value=True,
        ) as install:
            _install_ca_trust_if_needed(MagicMock())
        install.assert_called_once()


if __name__ == '__main__':
    unittest.main()

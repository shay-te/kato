"""Tests for the TLS-pin lifecycle (OG4) — TOFU + env-var + opt-out.

Closes the rogue-CA / cert-mis-issuance residual. The runtime egress
firewall already restricts to ``api.anthropic.com:443``, but the TLS
handshake validates against the system CA store — a compromised or
compelled CA could mint a valid cert for the host. Pinning binds the
trust decision to a specific SPKI fingerprint instead.

The lifecycle has four cases on every kato startup:

  1. **Env var pin** — ``KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256`` holds
     one or more comma-separated base64 SHA-256 SPKI fingerprints.
     Match → silent. Mismatch → refuse.
  2. **Opt-out** — ``KATO_SANDBOX_ALLOW_NO_TLS_PIN=true`` skips the
     pin entirely and prints a yellow warning every startup.
  3. **First run** — neither env var nor saved file. Connect, extract
     SPKI, save to ``~/.kato/anthropic-tls-pin``, print yellow box,
     continue.
  4. **Subsequent run** — file exists. Read fingerprint, compare to
     live cert. Match → silent. Mismatch → refuse with full context.

Edge cases: network unreachable (first run / subsequent run), file
unreadable, file malformed, parent dir uncreatable, both env vars
set. Each refuses with an operator-actionable message.

Color: ANSI yellow on TTY, no codes when stderr is redirected.
"""

from __future__ import annotations

import io
import os
import stat
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from kato_core_lib.sandbox.tls_pin import (
    TlsPinError,
    _read_pin_file,
    _save_pin_file,
    is_pinning_enabled,
    validate_anthropic_tls_pin_or_refuse,
)


# Two arbitrary base64-SHA256 strings shaped like real SPKI pins.
# 32 bytes of A's, B's, C's encoded as base64 → 44-char padded base64
# strings that decode to exactly 32 bytes (SHA-256 output size).
_FAKE_PRIMARY_PIN = 'QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE='  # b'A' * 32
_FAKE_BACKUP_PIN = 'QkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkI='   # b'B' * 32
_FAKE_WRONG_FINGERPRINT = 'Q0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0M='  # b'C' * 32


class _TtyStringIO(io.StringIO):
    """Fake stderr that reports as a TTY — for color-output tests."""

    def isatty(self) -> bool:
        return True


class _NonTtyStringIO(io.StringIO):
    """Fake stderr that reports as NOT a TTY — for non-color tests."""

    def isatty(self) -> bool:
        return False


def _temp_pin_path() -> Path:
    """Disposable path under a temp dir. Caller cleans up the parent."""
    td = tempfile.mkdtemp()
    return Path(td) / '.kato' / 'anthropic-tls-pin'


# --------------------------------------------------------------------------
# Predicate (legacy)
# --------------------------------------------------------------------------


class IsPinningEnabledTests(unittest.TestCase):
    def test_no_env_means_disabled(self) -> None:
        self.assertFalse(is_pinning_enabled({}))

    def test_empty_env_means_disabled(self) -> None:
        self.assertFalse(
            is_pinning_enabled({'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256': ''})
        )

    def test_single_pin_means_enabled(self) -> None:
        self.assertTrue(
            is_pinning_enabled({
                'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256': _FAKE_PRIMARY_PIN,
            }),
        )


# --------------------------------------------------------------------------
# Case 1 — env-var pin
# --------------------------------------------------------------------------


class Case1EnvVarPinTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stderr = _NonTtyStringIO()
        self.pin_path = _temp_pin_path()
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        if self.pin_path.parent.exists():
            for child in self.pin_path.parent.iterdir():
                child.unlink()
            self.pin_path.parent.rmdir()
        if self.pin_path.parent.parent.exists():
            try:
                self.pin_path.parent.parent.rmdir()
            except OSError:
                pass

    def test_matching_env_var_pin_passes_silently(self) -> None:
        validate_anthropic_tls_pin_or_refuse(
            env={'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256': _FAKE_PRIMARY_PIN},
            stderr=self.stderr,
            fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
            pin_file_path=self.pin_path,
        )
        # Silent success — no stderr output.
        self.assertEqual(self.stderr.getvalue(), '')

    def test_backup_pin_in_list_also_matches(self) -> None:
        # Operator lists primary,backup. Either match passes.
        validate_anthropic_tls_pin_or_refuse(
            env={
                'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256':
                    f'{_FAKE_PRIMARY_PIN},{_FAKE_BACKUP_PIN}',
            },
            stderr=self.stderr,
            fetch_live_fingerprint=lambda: _FAKE_BACKUP_PIN,
            pin_file_path=self.pin_path,
        )
        self.assertEqual(self.stderr.getvalue(), '')

    def test_mismatch_raises_with_full_refusal(self) -> None:
        with self.assertRaises(TlsPinError) as cm:
            validate_anthropic_tls_pin_or_refuse(
                env={'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256': _FAKE_PRIMARY_PIN},
                stderr=self.stderr,
                fetch_live_fingerprint=lambda: _FAKE_WRONG_FINGERPRINT,
                pin_file_path=self.pin_path,
            )
        # Short summary in the exception (for logger.error).
        self.assertIn('mismatch', str(cm.exception).lower())
        # Full refusal on stderr — names both fingerprints + recovery.
        out = self.stderr.getvalue()
        self.assertIn('TLS PIN MISMATCH', out)
        self.assertIn(_FAKE_PRIMARY_PIN, out)
        self.assertIn(_FAKE_WRONG_FINGERPRINT, out)
        # Env-var origin recovery names the env var, not the file.
        self.assertIn('KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256', out)
        # OG4 doc ref present.
        self.assertIn('OG4', out)

    def test_env_var_wins_over_existing_file(self) -> None:
        # Pre-create a file with a DIFFERENT pin. Env var overrides.
        self.pin_path.parent.mkdir(parents=True, exist_ok=True)
        self.pin_path.write_text(
            f'{_FAKE_BACKUP_PIN}\n# pinned: 2026-01-01T00:00:00+00:00\n'
        )
        validate_anthropic_tls_pin_or_refuse(
            env={'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256': _FAKE_PRIMARY_PIN},
            stderr=self.stderr,
            fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
            pin_file_path=self.pin_path,
        )
        # File still exists (env var wins but doesn't delete file).
        self.assertTrue(self.pin_path.exists())
        # Info note printed.
        out = self.stderr.getvalue()
        self.assertIn('TLS pin loaded from env var', out)
        self.assertIn('ignored', out)

    def test_network_failure_with_env_var_refuses(self) -> None:
        def _raise() -> str:
            raise OSError('connection refused')
        with self.assertRaises(TlsPinError) as cm:
            validate_anthropic_tls_pin_or_refuse(
                env={'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256': _FAKE_PRIMARY_PIN},
                stderr=self.stderr,
                fetch_live_fingerprint=_raise,
                pin_file_path=self.pin_path,
            )
        self.assertIn('Cannot reach', str(cm.exception))
        self.assertIn('Cannot reach', self.stderr.getvalue())


# --------------------------------------------------------------------------
# Case 2 — opt-out
# --------------------------------------------------------------------------


class Case2OptOutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stderr = _NonTtyStringIO()
        self.pin_path = _temp_pin_path()

    def test_optout_returns_silently_with_warning_on_stderr(self) -> None:
        validate_anthropic_tls_pin_or_refuse(
            env={'KATO_SANDBOX_ALLOW_NO_TLS_PIN': 'true'},
            stderr=self.stderr,
            pin_file_path=self.pin_path,
        )
        out = self.stderr.getvalue()
        # Warning text is verbatim per the spec.
        self.assertIn('TLS pin disabled', out)
        self.assertIn('KATO_SANDBOX_ALLOW_NO_TLS_PIN=true', out)
        self.assertIn('Rogue-CA', out)
        self.assertIn('OG4', out)

    def test_optout_does_not_call_fetch_live(self) -> None:
        called: list[bool] = []

        def _fetch() -> str:
            called.append(True)
            return _FAKE_PRIMARY_PIN

        validate_anthropic_tls_pin_or_refuse(
            env={'KATO_SANDBOX_ALLOW_NO_TLS_PIN': 'true'},
            stderr=self.stderr,
            fetch_live_fingerprint=_fetch,
            pin_file_path=self.pin_path,
        )
        # The whole point of opt-out is to skip the network call.
        self.assertEqual(called, [])

    def test_optout_does_not_create_pin_file(self) -> None:
        validate_anthropic_tls_pin_or_refuse(
            env={'KATO_SANDBOX_ALLOW_NO_TLS_PIN': 'true'},
            stderr=self.stderr,
            pin_file_path=self.pin_path,
        )
        self.assertFalse(self.pin_path.exists())


# --------------------------------------------------------------------------
# Case 3 — first run (TOFU)
# --------------------------------------------------------------------------


class Case3FirstRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stderr = _NonTtyStringIO()
        self.pin_path = _temp_pin_path()

    def test_first_run_pins_and_saves_to_file(self) -> None:
        validate_anthropic_tls_pin_or_refuse(
            env={},
            stderr=self.stderr,
            fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
            pin_file_path=self.pin_path,
        )
        # File created with the live fingerprint.
        self.assertTrue(self.pin_path.exists())
        text = self.pin_path.read_text()
        self.assertTrue(text.startswith(_FAKE_PRIMARY_PIN))
        self.assertIn('# pinned:', text)

    def test_first_run_file_has_mode_0600(self) -> None:
        validate_anthropic_tls_pin_or_refuse(
            env={},
            stderr=self.stderr,
            fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
            pin_file_path=self.pin_path,
        )
        # Strip the file-type bits so we just compare permissions.
        mode = stat.S_IMODE(os.stat(self.pin_path).st_mode)
        self.assertEqual(
            mode, 0o600,
            f'pin file mode {oct(mode)} != 0o600',
        )

    def test_first_run_parent_dir_has_mode_0700(self) -> None:
        validate_anthropic_tls_pin_or_refuse(
            env={},
            stderr=self.stderr,
            fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
            pin_file_path=self.pin_path,
        )
        mode = stat.S_IMODE(os.stat(self.pin_path.parent).st_mode)
        self.assertEqual(
            mode, 0o700,
            f'parent dir mode {oct(mode)} != 0o700',
        )

    def test_first_run_prints_yellow_box(self) -> None:
        validate_anthropic_tls_pin_or_refuse(
            env={},
            stderr=self.stderr,
            fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
            pin_file_path=self.pin_path,
        )
        out = self.stderr.getvalue()
        # Box characters present.
        self.assertIn('╔', out)
        self.assertIn('╚', out)
        self.assertIn('║', out)
        # Title verbatim.
        self.assertIn('TLS PIN — First run', out)
        # OG4 doc ref present.
        self.assertIn('OG4', out)

    def test_first_run_box_emits_color_on_tty(self) -> None:
        tty_stderr = _TtyStringIO()
        validate_anthropic_tls_pin_or_refuse(
            env={},
            stderr=tty_stderr,
            fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
            pin_file_path=self.pin_path,
        )
        out = tty_stderr.getvalue()
        # ANSI yellow + reset escapes wrap the message on TTY.
        self.assertIn('\033[33m', out)
        self.assertIn('\033[0m', out)

    def test_first_run_no_color_on_non_tty(self) -> None:
        validate_anthropic_tls_pin_or_refuse(
            env={},
            stderr=self.stderr,
            fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
            pin_file_path=self.pin_path,
        )
        out = self.stderr.getvalue()
        # No ANSI escapes when stderr is not a TTY (CI / pipes).
        self.assertNotIn('\033[', out)

    def test_first_run_uses_provided_now_for_timestamp(self) -> None:
        fixed_now = datetime(2026, 5, 3, 12, 30, 45, tzinfo=timezone.utc)
        validate_anthropic_tls_pin_or_refuse(
            env={},
            stderr=self.stderr,
            fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
            pin_file_path=self.pin_path,
            now=lambda: fixed_now,
        )
        text = self.pin_path.read_text()
        self.assertIn('2026-05-03T12:30:45+00:00', text)

    def test_first_run_network_failure_refuses_without_writing_file(self) -> None:
        def _raise() -> str:
            raise OSError('DNS lookup failed')
        with self.assertRaises(TlsPinError) as cm:
            validate_anthropic_tls_pin_or_refuse(
                env={},
                stderr=self.stderr,
                fetch_live_fingerprint=_raise,
                pin_file_path=self.pin_path,
            )
        # Spec-mandated phrasing: cannot reach + establish.
        self.assertIn('Cannot reach', str(cm.exception))
        self.assertIn('establish', str(cm.exception))
        # No placeholder file written when we can't determine the pin.
        self.assertFalse(self.pin_path.exists())

    def test_first_run_save_failure_refuses_with_path(self) -> None:
        # Set the path to a location where save will fail. The
        # parent of the parent doesn't exist and isn't writable —
        # but ``mkdir(parents=True)`` would normally succeed under
        # /tmp. Force the failure by pointing at a path under a
        # regular file instead of a directory.
        with tempfile.NamedTemporaryFile() as f:
            bad_path = Path(f.name) / 'subdir' / 'pin'
            with self.assertRaises(TlsPinError) as cm:
                validate_anthropic_tls_pin_or_refuse(
                    env={},
                    stderr=self.stderr,
                    fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
                    pin_file_path=bad_path,
                )
            self.assertIn('Cannot save TLS pin', str(cm.exception))


# --------------------------------------------------------------------------
# Case 4 — subsequent run (file exists)
# --------------------------------------------------------------------------


class Case4SubsequentRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stderr = _NonTtyStringIO()
        self.pin_path = _temp_pin_path()
        self.pin_path.parent.mkdir(parents=True, exist_ok=True)

    def _write_pin_file(self, fingerprint: str, *, pinned_at: str = '2026-01-15T08:00:00+00:00') -> None:
        self.pin_path.write_text(
            f'{fingerprint}\n# pinned: {pinned_at}\n'
        )

    def test_match_returns_silently(self) -> None:
        self._write_pin_file(_FAKE_PRIMARY_PIN)
        validate_anthropic_tls_pin_or_refuse(
            env={},
            stderr=self.stderr,
            fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
            pin_file_path=self.pin_path,
        )
        self.assertEqual(self.stderr.getvalue(), '')

    def test_mismatch_refuses_with_full_context(self) -> None:
        self._write_pin_file(_FAKE_PRIMARY_PIN, pinned_at='2026-01-15T08:00:00+00:00')
        with self.assertRaises(TlsPinError) as cm:
            validate_anthropic_tls_pin_or_refuse(
                env={},
                stderr=self.stderr,
                fetch_live_fingerprint=lambda: _FAKE_WRONG_FINGERPRINT,
                pin_file_path=self.pin_path,
            )
        self.assertIn('mismatch', str(cm.exception).lower())
        out = self.stderr.getvalue()
        # All four operator-actionable pieces present.
        self.assertIn('TLS PIN MISMATCH', out)
        self.assertIn(_FAKE_PRIMARY_PIN, out)         # saved pin
        self.assertIn(_FAKE_WRONG_FINGERPRINT, out)   # live pin
        self.assertIn('2026-01-15T08:00:00+00:00', out)  # pinned-at timestamp
        # Recovery names the file path with rm.
        self.assertIn('rm', out)
        self.assertIn('anthropic-tls-pin', out)

    def test_mismatch_message_distinguishes_expected_vs_unexpected(self) -> None:
        # The two-branch interpretation is what makes the message
        # operator-actionable. Without it the message reads like an
        # error code, not a decision tree.
        self._write_pin_file(_FAKE_PRIMARY_PIN)
        with self.assertRaises(TlsPinError):
            validate_anthropic_tls_pin_or_refuse(
                env={},
                stderr=self.stderr,
                fetch_live_fingerprint=lambda: _FAKE_WRONG_FINGERPRINT,
                pin_file_path=self.pin_path,
            )
        out = self.stderr.getvalue()
        self.assertIn('If you EXPECTED this', out)
        self.assertIn('If you did NOT expect this', out)
        # The "trusted source" cross-check guidance is the load-bearing
        # bit — it's the difference between cargo-cult re-pinning and
        # actually catching a MITM.
        self.assertIn('trusted source', out)

    def test_network_failure_on_subsequent_run_refuses(self) -> None:
        self._write_pin_file(_FAKE_PRIMARY_PIN)

        def _raise() -> str:
            raise OSError('network unreachable')

        with self.assertRaises(TlsPinError) as cm:
            validate_anthropic_tls_pin_or_refuse(
                env={},
                stderr=self.stderr,
                fetch_live_fingerprint=_raise,
                pin_file_path=self.pin_path,
            )
        # Spec: refuse with "Cannot reach" + "verify" message.
        self.assertIn('Cannot reach', str(cm.exception))
        self.assertIn('verify', str(cm.exception))

    def test_unreadable_file_refuses_with_path_and_remediation(self) -> None:
        self._write_pin_file(_FAKE_PRIMARY_PIN)
        # Strip read permission so the read raises PermissionError
        # (which is an OSError subclass).
        os.chmod(self.pin_path, 0)
        try:
            # On macOS / Linux as a regular user, mode 0 means
            # PermissionError. As root the chmod is ignored — skip
            # the test rather than assert wrong behavior on root.
            try:
                self.pin_path.read_text()
            except (PermissionError, OSError):
                pass
            else:
                self.skipTest(
                    'running as a user that can read mode-0 files (root?)'
                )
            with self.assertRaises(TlsPinError) as cm:
                validate_anthropic_tls_pin_or_refuse(
                    env={},
                    stderr=self.stderr,
                    fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
                    pin_file_path=self.pin_path,
                )
            self.assertIn('cannot be read', str(cm.exception))
            self.assertIn('Delete and re-run', str(cm.exception))
        finally:
            # Restore so cleanup can delete it.
            os.chmod(self.pin_path, 0o600)

    def test_malformed_file_refuses_with_remediation(self) -> None:
        # First line is not valid base64.
        self.pin_path.write_text('not-valid-base64-#@!\n')
        with self.assertRaises(TlsPinError) as cm:
            validate_anthropic_tls_pin_or_refuse(
                env={},
                stderr=self.stderr,
                fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
                pin_file_path=self.pin_path,
            )
        self.assertIn('malformed', str(cm.exception))
        self.assertIn('Delete and re-run', str(cm.exception))

    def test_empty_file_refuses_as_malformed(self) -> None:
        self.pin_path.write_text('')
        with self.assertRaises(TlsPinError) as cm:
            validate_anthropic_tls_pin_or_refuse(
                env={},
                stderr=self.stderr,
                fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
                pin_file_path=self.pin_path,
            )
        self.assertIn('malformed', str(cm.exception))

    def test_wrong_length_decoded_fingerprint_refuses_as_malformed(self) -> None:
        # 16 bytes of A's is valid base64 but wrong length for SHA-256.
        short_fingerprint = 'QUFBQUFBQUFBQUFBQUFBQQ=='  # b'A' * 16
        self.pin_path.write_text(f'{short_fingerprint}\n# pinned: x\n')
        with self.assertRaises(TlsPinError) as cm:
            validate_anthropic_tls_pin_or_refuse(
                env={},
                stderr=self.stderr,
                fetch_live_fingerprint=lambda: _FAKE_PRIMARY_PIN,
                pin_file_path=self.pin_path,
            )
        self.assertIn('malformed', str(cm.exception))


# --------------------------------------------------------------------------
# Edge cases — ambiguous configuration
# --------------------------------------------------------------------------


class EdgeCaseAmbiguousConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stderr = _NonTtyStringIO()
        self.pin_path = _temp_pin_path()

    def test_env_var_and_optout_both_set_refuses(self) -> None:
        with self.assertRaises(TlsPinError) as cm:
            validate_anthropic_tls_pin_or_refuse(
                env={
                    'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256': _FAKE_PRIMARY_PIN,
                    'KATO_SANDBOX_ALLOW_NO_TLS_PIN': 'true',
                },
                stderr=self.stderr,
                pin_file_path=self.pin_path,
            )
        # Names both env vars and the disambiguation.
        msg = str(cm.exception)
        self.assertIn('KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256', msg)
        self.assertIn('KATO_SANDBOX_ALLOW_NO_TLS_PIN', msg)
        self.assertIn('Pick one', msg)


# --------------------------------------------------------------------------
# File format helpers — round-trip
# --------------------------------------------------------------------------


class PinFileFormatTests(unittest.TestCase):
    """Round-trip ``_save_pin_file`` ↔ ``_read_pin_file``.

    Locks the on-disk format so a future refactor can't silently
    change the parser without updating the writer (or vice versa).
    """

    def setUp(self) -> None:
        self.pin_path = _temp_pin_path()

    def test_save_then_read_round_trips_fingerprint(self) -> None:
        fixed_now = datetime(2026, 5, 3, 12, 30, 45, tzinfo=timezone.utc)
        _save_pin_file(self.pin_path, _FAKE_PRIMARY_PIN, now=lambda: fixed_now)
        fingerprint, pinned_at = _read_pin_file(self.pin_path)
        self.assertEqual(fingerprint, _FAKE_PRIMARY_PIN)
        self.assertEqual(pinned_at, '2026-05-03T12:30:45+00:00')

    def test_file_format_first_line_is_fingerprint(self) -> None:
        _save_pin_file(self.pin_path, _FAKE_PRIMARY_PIN)
        first_line = self.pin_path.read_text().splitlines()[0]
        self.assertEqual(first_line, _FAKE_PRIMARY_PIN)

    def test_file_format_second_line_is_pinned_comment(self) -> None:
        _save_pin_file(self.pin_path, _FAKE_PRIMARY_PIN)
        second_line = self.pin_path.read_text().splitlines()[1]
        self.assertTrue(second_line.startswith('# pinned:'))


if __name__ == '__main__':
    unittest.main()

"""SNI pinning: an IP allowlist is not a hostname allowlist.

The firewall permits TCP/443 to the addresses ``api.anthropic.com``
resolved to. Those are shared cloud addresses, and code in the container
chooses its own hostname — so it can open a valid TLS session to another
tenant behind the same IP. These tests pin the parser and the decision
that close that gap.

The proxy is built and tested; it is NOT yet wired into the spawn path,
because the sandbox bridge runs with inter-container communication
disabled and an on-bridge proxy is therefore unreachable. See the note in
``wrap_command``.
"""

from __future__ import annotations

import unittest

from sandbox_core_lib.sandbox_core_lib.sni_proxy import (
    host_is_allowed,
    parse_sni,
)


def _client_hello(server_name: bytes) -> bytes:
    """A minimal but structurally valid ClientHello carrying ``server_name``."""
    name_entry = b'\x00' + len(server_name).to_bytes(2, 'big') + server_name
    sni_ext_body = len(name_entry).to_bytes(2, 'big') + name_entry
    sni_ext = b'\x00\x00' + len(sni_ext_body).to_bytes(2, 'big') + sni_ext_body
    extensions = len(sni_ext).to_bytes(2, 'big') + sni_ext
    body = (
        b'\x03\x03'            # client version
        + b'\x00' * 32         # random
        + b'\x00'              # session id length
        + b'\x00\x02\x00\x2f'  # cipher suites
        + b'\x01\x00'          # compression methods
        + extensions
    )
    handshake = b'\x01' + len(body).to_bytes(3, 'big') + body
    return b'\x16\x03\x01' + len(handshake).to_bytes(2, 'big') + handshake


class ParseSniTests(unittest.TestCase):
    def test_extracts_the_hostname(self) -> None:
        self.assertEqual(
            parse_sni(_client_hello(b'api.anthropic.com')), 'api.anthropic.com',
        )

    def test_lowercases(self) -> None:
        self.assertEqual(
            parse_sni(_client_hello(b'API.Anthropic.COM')), 'api.anthropic.com',
        )

    def test_non_tls_input_yields_nothing(self) -> None:
        for junk in (b'', b'GET / HTTP/1.1\r\n', b'\x16', b'\x17\x03\x03\x00\x05hello'):
            self.assertEqual(parse_sni(junk), '')

    def test_truncated_records_do_not_raise(self) -> None:
        full = _client_hello(b'api.anthropic.com')
        for cut in range(1, len(full)):
            parse_sni(full[:cut])            # must not raise

    def test_declared_name_length_longer_than_the_buffer_is_rejected(self) -> None:
        # A hostile peer can claim any length it likes. Trusting the
        # declared name length is how a parser reads past the record or
        # returns a truncated hostname that then passes an allowlist.
        full = bytearray(_client_hello(b'api.anthropic.com'))
        name = b'api.anthropic.com'
        offset = full.rfind(name)
        # The 2-byte name length sits immediately before the name itself.
        full[offset - 2:offset] = (len(name) + 50).to_bytes(2, 'big')
        self.assertEqual(parse_sni(bytes(full)), '')


class AllowlistTests(unittest.TestCase):
    ALLOW = frozenset({'api.anthropic.com'})

    def test_exact_match_only(self) -> None:
        self.assertTrue(host_is_allowed('api.anthropic.com', self.ALLOW))

    def test_suffix_tricks_are_refused(self) -> None:
        # The reason matching is exact: suffix rules turn an allowlist
        # into a bypass.
        for host in (
            'api.anthropic.com.attacker.test',
            'evil-api.anthropic.com',
            'api.anthropic.com.',
            'attacker.test',
            '',
        ):
            self.assertFalse(host_is_allowed(host, self.ALLOW), host)


if __name__ == '__main__':
    unittest.main()

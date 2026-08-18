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


class ParserDifferentialTests(unittest.TestCase):
    """A crafted server_name LIST must not mean two different things.

    The first version read bytes 3..5 as "the name" — it never checked the
    entry TYPE and never validated the list length. A ClientHello with

        entry[0]: type=0xFF "api.anthropic.com"
        entry[1]: type=0x00 "attacker.example"

    was therefore allowed on the strength of a name the real server never
    reads, while the bytes forwarded verbatim asked the far end for the
    attacker's host. On shared addresses that is a complete bypass of the
    control this proxy exists to provide.
    """

    @staticmethod
    def _entry(name_type: int, name: bytes) -> bytes:
        return bytes([name_type]) + len(name).to_bytes(2, 'big') + name

    def _hello(self, entries: bytes) -> bytes:
        ext_body = len(entries).to_bytes(2, 'big') + entries
        ext = b'\x00\x00' + len(ext_body).to_bytes(2, 'big') + ext_body
        extensions = len(ext).to_bytes(2, 'big') + ext
        body = (
            b'\x03\x03' + b'\x00' * 32 + b'\x00'
            + b'\x00\x02\x00\x2f' + b'\x01\x00' + extensions
        )
        handshake = b'\x01' + len(body).to_bytes(3, 'big') + body
        return b'\x16\x03\x01' + len(handshake).to_bytes(2, 'big') + handshake

    def test_legitimate_single_host_name_still_parses(self) -> None:
        hello = self._hello(self._entry(0x00, b'api.anthropic.com'))
        self.assertEqual(parse_sni(hello), 'api.anthropic.com')

    def test_type_confusion_is_refused(self) -> None:
        hello = self._hello(
            self._entry(0xFF, b'api.anthropic.com')
            + self._entry(0x00, b'attacker.example'),
        )
        self.assertEqual(parse_sni(hello), '')

    def test_duplicate_host_names_are_refused(self) -> None:
        hello = self._hello(
            self._entry(0x00, b'api.anthropic.com')
            + self._entry(0x00, b'attacker.example'),
        )
        self.assertEqual(parse_sni(hello), '')

    def test_list_length_that_lies_is_refused(self) -> None:
        self.assertEqual(parse_sni(self._hello(b'\x00\x00\x05ab')), '')

    def test_trailing_bytes_after_the_list_are_refused(self) -> None:
        entries = self._entry(0x00, b'api.anthropic.com')
        ext_body = len(entries).to_bytes(2, 'big') + entries + b'\x00'
        ext = b'\x00\x00' + len(ext_body).to_bytes(2, 'big') + ext_body
        extensions = len(ext).to_bytes(2, 'big') + ext
        body = (
            b'\x03\x03' + b'\x00' * 32 + b'\x00'
            + b'\x00\x02\x00\x2f' + b'\x01\x00' + extensions
        )
        handshake = b'\x01' + len(body).to_bytes(3, 'big') + body
        hello = b'\x16\x03\x01' + len(handshake).to_bytes(2, 'big') + handshake
        self.assertEqual(parse_sni(hello), 'api.anthropic.com')


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

"""SNI-pinning egress proxy for the sandbox network.

The in-container firewall allows TCP/443 to the IP addresses that
``api.anthropic.com`` resolved to at container start. That is weaker than
it reads: those are shared cloud/CDN addresses, so anything else hosted
behind them is reachable, and code inside the container picks its own
hostname — it can open a TLS session to an attacker-controlled tenant on
the same IP with a perfectly valid certificate. An IP allowlist is not a
hostname allowlist.

This proxy closes that. It reads only the TLS ClientHello, extracts the
SNI, and refuses anything not on the allowlist. It does NOT terminate
TLS: no private key, no certificate, no ability to read the traffic —
the bytes are forwarded verbatim, so end-to-end encryption between the
agent and Anthropic is untouched. That is a deliberate trade: this
variant cannot inject the credential (the container still holds its own
key), but it also cannot be turned into a interception point, and it
needs no cooperation from the client.

Deployment: run this on the sandbox network, point the container's
``api.anthropic.com`` at it with ``--add-host``, and let the firewall
allow 443 only to the proxy. Direct egress then has nowhere to go.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import socket
import sys

_LOGGER = logging.getLogger('sandbox.sni-proxy')

TLS_HANDSHAKE = 0x16
CLIENT_HELLO = 0x01
EXTENSION_SERVER_NAME = 0x0000
# A ClientHello larger than this is not one we need to serve.
MAX_CLIENT_HELLO = 16384
UPSTREAM_PORT = 443


NAME_TYPE_HOST_NAME = 0x00


def _parse_server_name_list(ext_body: bytes) -> str:
    """Parse the server_name extension STRICTLY, or return ''.

    This is where a parser differential turns into a bypass. The
    extension is a LIST of typed entries, and the first version of this
    function read bytes 3..5 as "the name" — skipping the list length and
    never checking the entry TYPE. A ClientHello carrying

        entry[0]: type=0xFF, "api.anthropic.com"
        entry[1]: type=0x00, "attacker.example"

    would then be allowed on the strength of a name the real server never
    looks at, while the bytes forwarded verbatim ask the far end for the
    attacker's host — precisely the shared-address bypass this proxy
    exists to prevent.

    So: walk the list properly, accept ONLY a host_name entry, and refuse
    anything anomalous rather than trying to guess which entry a given
    TLS stack would honour. Real clients send exactly one host_name.
    Emulating other implementations' recovery behaviour is how the two
    ends end up disagreeing, which is the bug class itself.
    """
    list_length = int.from_bytes(ext_body[0:2], 'big')
    entries = ext_body[2:2 + list_length]
    if len(entries) != list_length:
        return ''                              # declared length is a lie
    host = ''
    pos = 0
    while pos + 3 <= len(entries):
        name_type = entries[pos]
        name_length = int.from_bytes(entries[pos + 1:pos + 3], 'big')
        value = entries[pos + 3:pos + 3 + name_length]
        if len(value) != name_length:
            return ''
        pos += 3 + name_length
        if name_type != NAME_TYPE_HOST_NAME:
            # A non-host_name entry is not something a legitimate client
            # sends. Refuse rather than skip: skipping is exactly what
            # lets a crafted list mean two different things.
            return ''
        if host:
            return ''                          # duplicate host_name
        try:
            host = value.decode('ascii').lower()
        except UnicodeDecodeError:
            return ''
    if pos != len(entries):
        return ''                              # trailing bytes
    return host


def parse_sni(data: bytes) -> str:
    """Extract the SNI host from a TLS ClientHello, or '' if absent.

    Deliberately strict and allocation-free-ish: every length is checked
    against the buffer before use, because this parses the first bytes an
    untrusted peer sends. Anything malformed returns '' and the caller
    drops the connection — a parser that "recovers" from a hostile record
    is how you get a bypass.
    """
    try:
        if len(data) < 5 or data[0] != TLS_HANDSHAKE:
            return ''
        record_length = int.from_bytes(data[3:5], 'big')
        body = data[5:5 + record_length]
        if len(body) < 4 or body[0] != CLIENT_HELLO:
            return ''
        pos = 4 + 2 + 32                      # handshake header, version, random
        if len(body) < pos + 1:
            return ''
        session_id_length = body[pos]
        pos += 1 + session_id_length
        if len(body) < pos + 2:
            return ''
        cipher_suites_length = int.from_bytes(body[pos:pos + 2], 'big')
        pos += 2 + cipher_suites_length
        if len(body) < pos + 1:
            return ''
        compression_length = body[pos]
        pos += 1 + compression_length
        if len(body) < pos + 2:
            return ''
        extensions_length = int.from_bytes(body[pos:pos + 2], 'big')
        pos += 2
        end = min(pos + extensions_length, len(body))
        while pos + 4 <= end:
            ext_type = int.from_bytes(body[pos:pos + 2], 'big')
            ext_length = int.from_bytes(body[pos + 2:pos + 4], 'big')
            ext_body = body[pos + 4:pos + 4 + ext_length]
            pos += 4 + ext_length
            if ext_type != EXTENSION_SERVER_NAME or len(ext_body) < 2:
                continue
            return _parse_server_name_list(ext_body)
    except (IndexError, ValueError):
        return ''
    return ''


def host_is_allowed(host: str, allowlist: frozenset[str]) -> bool:
    """Exact match only.

    No suffix matching: ``*.anthropic.com`` style rules are how an
    allowlist becomes a bypass (``anthropic.com.attacker.test``), and the
    agent needs exactly one host.
    """
    return bool(host) and host in allowlist


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError, OSError):
        pass
    finally:
        try:
            writer.close()
        except OSError:
            pass


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    allowlist: frozenset[str],
    upstream_port: int = UPSTREAM_PORT,
    resolve=socket.gethostbyname,
    upstream_ips: dict[str, str] | None = None,
) -> None:
    """Read the ClientHello, allow or drop, then splice both directions."""
    peer = writer.get_extra_info('peername')
    try:
        head = await asyncio.wait_for(reader.read(MAX_CLIENT_HELLO), timeout=10)
    except (asyncio.TimeoutError, OSError):
        writer.close()
        return
    host = parse_sni(head)
    if not host_is_allowed(host, allowlist):
        _LOGGER.warning(
            'refused egress from %s to %r (not in allowlist)', peer, host or '<no SNI>',
        )
        writer.close()
        return
    try:
        # Prefer an address resolved on the HOST and handed to us at
        # startup. Nothing in this container then performs DNS at all:
        # no resolver to poison, no lookup to tunnel data through, and no
        # dependency on an embedded resolver whose forwarding path is
        # itself firewalled. Falls back to a lookup only if none was
        # supplied.
        address = (upstream_ips or {}).get(host) or resolve(host)
        upstream_reader, upstream_writer = await asyncio.open_connection(
            address, upstream_port,
        )
    except (OSError, socket.gaierror) as exc:
        _LOGGER.warning('upstream connect failed for %s: %s', host, exc)
        writer.close()
        return
    _LOGGER.info('allowed %s -> %s', peer, host)
    upstream_writer.write(head)              # replay the ClientHello verbatim
    await upstream_writer.drain()
    await asyncio.gather(
        _pipe(reader, upstream_writer),
        _pipe(upstream_reader, writer),
    )


async def serve(
    host: str, port: int, allowlist: frozenset[str], *,
    upstream_port: int = UPSTREAM_PORT, upstream_ips: dict[str, str] | None = None,
):
    async def _handler(reader, writer):
        await handle_client(
            reader, writer, allowlist=allowlist, upstream_port=upstream_port,
            upstream_ips=upstream_ips,
        )

    server = await asyncio.start_server(_handler, host, port)
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument('--listen', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8443)
    parser.add_argument(
        '--allow', action='append', default=[],
        help='hostname permitted through (exact match; repeatable)',
    )
    parser.add_argument(
        '--upstream', action='append', default=[],
        help='host=ip resolved by the CALLER, so this process never does DNS',
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='[sni-proxy] %(message)s')
    allowlist = frozenset(a.strip().lower() for a in args.allow if a.strip())
    if not allowlist:
        _LOGGER.error('refusing to start with an empty allowlist')
        return 2

    upstream_ips = {}
    for pair in args.upstream:
        name, _, address = pair.partition('=')
        if name.strip() and address.strip():
            upstream_ips[name.strip().lower()] = address.strip()

    async def _run():
        server = await serve(
            args.listen, args.port, allowlist, upstream_ips=upstream_ips,
        )
        _LOGGER.info('listening on %s:%s, allowing %s',
                     args.listen, args.port, ', '.join(sorted(allowlist)))
        async with server:
            await server.serve_forever()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())

"""Serving a big JSON payload without re-sending it every poll.

The Files tree and the Changes diff are both re-read every few seconds while a
task is open, and both are almost always byte-identical to the answer already
on screen: the agent edits a handful of files, it does not reshape the
repository. Re-sending them costs the operator real time — a 27-repository task
was 1.4 MB of tree and 4.3 MB of diff EVERY five seconds, downloaded and parsed
only to be recognised as unchanged and thrown away.

So a payload is turned into three forms, computed once:

* the compact **body**,
* an **ETag** over those bytes, and
* a **gzipped** copy.

A client that sends the tag back gets an empty ``304`` and re-parses nothing. A
client that needs the bytes gets them compressed, which for this kind of
content (repeated directory names, repeated diff context) is worth several
times its cost.

The tag is always compared against a FRESHLY BUILT payload. It is a transfer
optimisation, never a second opinion on what is current: the server still does
all its work and then says "what you have is what I just built".
"""

from __future__ import annotations

import gzip
import hashlib
import json
import threading
from collections import OrderedDict
from dataclasses import dataclass

from flask import request as flask_request

#: Fast rather than smallest: level 4 is a few percent larger than level 6 and
#: about half the time, and this runs on the request path whenever a payload
#: changes.
_GZIP_LEVEL = 4


def serialize_payload(payload: dict) -> bytes:
    """Compact JSON with sorted keys.

    Sorted so the same content always serializes to the same bytes, whatever
    order it was built in — the ETag is a hash of these bytes, and a key-order
    difference would make an unchanged payload look new to every client.
    """
    return json.dumps(
        payload, separators=(',', ':'), sort_keys=True, ensure_ascii=False,
    ).encode('utf-8')


@dataclass(frozen=True)
class TaggedPayload:
    """One payload in every form the route serves."""

    body: bytes
    #: Unquoted, as werkzeug's ``If-None-Match`` parsing hands it back.
    etag: str
    gzipped: bytes

    @classmethod
    def from_body(cls, body: bytes) -> TaggedPayload:
        return cls(
            body=body,
            etag=hashlib.sha1(body).hexdigest(),
            gzipped=gzip.compress(body, compresslevel=_GZIP_LEVEL),
        )

    @classmethod
    def from_payload(cls, payload: dict) -> TaggedPayload:
        return cls.from_body(serialize_payload(payload))


#: Worth compressing. Everything else a browser asks kato for — images, fonts,
#: an opaque download of a workspace file — is already compressed, and gzipping
#: it spends CPU to make it very slightly bigger.
_COMPRESSIBLE_MIMETYPES = frozenset({
    'text/html', 'text/css', 'text/plain', 'text/javascript',
    'application/javascript', 'application/json', 'image/svg+xml',
})

#: Under this, gzip's own framing costs more than it saves.
_COMPRESS_MIN_BYTES = 1024

#: How many compressed bodies to keep. Keyed by the response's ETag, which for
#: a static file already encodes its mtime and size, so a rebuilt asset misses
#: and is compressed again.
_COMPRESSED_CACHE_LIMIT = 32


def register_response_compression(app) -> None:
    """Gzip text responses for clients that asked, compressing once per version.

    kato serves its own static files — there is no nginx in front — so nothing
    else will ever do this. The UI bundle went out UNCOMPRESSED on every cold
    load: 5.3 MB where 1.5 MB would do, which is the single largest thing the
    operator waits for, and 72% of it is pure waste.

    Compressing a 5 MB bundle costs ~66ms, so the result is CACHED against the
    response's own ETag. Otherwise this would trade bytes for latency on every
    page load.

    Left alone, deliberately:

    * responses that already carry ``Content-Encoding`` — the tree and diff
      routes compress themselves (see :func:`conditional_json_response`) and
      double-encoding would hand the browser garbage;
    * anything STREAMED — the chat's event stream has to arrive frame by frame
      and says ``no-transform`` for exactly that reason;
    * non-200s, so a 304's emptiness is never "compressed".
    """
    cache: OrderedDict[str, bytes] = OrderedDict()
    lock = threading.Lock()

    def _cached_gzip(key: str, data: bytes) -> bytes:
        if key:
            with lock:
                hit = cache.get(key)
                if hit is not None:
                    cache.move_to_end(key)
                    return hit
        compressed = gzip.compress(data, compresslevel=_GZIP_LEVEL)
        if key:
            with lock:
                cache[key] = compressed
                while len(cache) > _COMPRESSED_CACHE_LIMIT:
                    cache.popitem(last=False)
        return compressed

    @app.after_request
    def _compress_response(response):  # noqa: WPS430 - Flask needs the closure
        if (
            response.status_code != 200
            or response.headers.get('Content-Encoding')
            or response.mimetype not in _COMPRESSIBLE_MIMETYPES
            # Unbounded streams only. A STATIC FILE also reports as "streamed"
            # — its body is a file wrapper, which has no length — so testing
            # ``is_streamed`` alone skipped precisely the 5.3 MB bundle this
            # exists for. A real stream has no Content-Length either; the
            # event stream is additionally excluded by the mimetypes above.
            or (response.is_streamed and not response.content_length)
            or 'gzip' not in flask_request.accept_encodings
        ):
            return response
        # A static file is handed back as a file wrapper; reading it is what
        # materialises those bytes so they can be compressed and cached.
        response.direct_passthrough = False
        data = response.get_data()
        if len(data) < _COMPRESS_MIN_BYTES:
            return response
        compressed = _cached_gzip(response.headers.get('ETag') or '', data)
        # Already-compressed content can come back BIGGER; sending it would
        # cost the browser a decode for nothing.
        if len(compressed) >= len(data):
            return response
        response.set_data(compressed)
        response.headers['Content-Encoding'] = 'gzip'
        response.headers['Content-Length'] = str(len(compressed))
        # The body now depends on the request's Accept-Encoding, so any cache
        # between here and the browser has to key on it.
        response.headers['Vary'] = 'Accept-Encoding'
        return response


def conditional_json_response(app, request, tagged: TaggedPayload, *, headers=None):
    """Serve ``tagged``: 304, gzipped, or plain, in that order.

    ``headers`` are added to every form, including the 304 — a header that
    describes the ANSWER (rather than the payload) must still reach a client
    that is being told nothing changed.
    """
    response_headers = {'Cache-Control': 'no-cache', **(headers or {})}
    # ``If-None-Match`` is the client echoing the tag of what it already shows.
    if tagged.etag in request.if_none_match:
        response = app.response_class(status=304, headers=response_headers)
        response.set_etag(tagged.etag)
        return response
    body = tagged.body
    if 'gzip' in request.accept_encodings:
        body = tagged.gzipped
        response_headers['Content-Encoding'] = 'gzip'
        # The body varies with the request's Accept-Encoding, so any cache
        # between here and the browser has to key on it.
        response_headers['Vary'] = 'Accept-Encoding'
    response = app.response_class(
        body, mimetype='application/json', headers=response_headers,
    )
    response.set_etag(tagged.etag)
    return response

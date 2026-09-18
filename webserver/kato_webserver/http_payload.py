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
from dataclasses import dataclass

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

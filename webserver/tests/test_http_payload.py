"""Serving a big JSON payload without re-sending it on every poll.

The Files tree and the Changes diff are both re-read every few seconds while a
task is open and are almost always byte-identical to what the pane already
shows. Measured on one 27-repository task: 1.4 MB of tree and 4.3 MB of diff,
every five seconds, downloaded and parsed only to be thrown away as unchanged.

A real Flask app and real request contexts — the whole point is what the
headers do, so stubbing them would test nothing.
"""

from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from unittest.mock import patch

from kato_webserver import http_payload
from kato_webserver.http_payload import (
    TaggedPayload,
    conditional_json_response,
    register_response_compression,
    serialize_payload,
)

PAYLOAD = {'diffs': [{'repo_id': 'client', 'diff': 'a' * 500}], 'repository_ids': ['client']}


class SerializePayloadTests(unittest.TestCase):
    def test_it_is_compact(self) -> None:
        self.assertNotIn(b', ', serialize_payload({'a': 1, 'b': 2}))

    def test_key_order_does_not_change_the_bytes(self) -> None:
        # The ETag is a hash of these bytes. Without sorting, the same content
        # built in a different order would look like a change to every client.
        self.assertEqual(
            serialize_payload({'a': 1, 'b': 2}), serialize_payload({'b': 2, 'a': 1}),
        )

    def test_it_round_trips(self) -> None:
        self.assertEqual(json.loads(serialize_payload(PAYLOAD)), PAYLOAD)

    def test_non_ascii_survives(self) -> None:
        payload = {'name': 'ünïcødé.txt'}
        self.assertEqual(json.loads(serialize_payload(payload)), payload)


class TaggedPayloadTests(unittest.TestCase):
    def test_the_gzipped_form_decompresses_to_the_body(self) -> None:
        tagged = TaggedPayload.from_payload(PAYLOAD)
        self.assertEqual(gzip.decompress(tagged.gzipped), tagged.body)

    def test_the_same_payload_gets_the_same_tag(self) -> None:
        first = TaggedPayload.from_payload(PAYLOAD)
        again = TaggedPayload.from_payload(dict(PAYLOAD))
        self.assertEqual(first.etag, again.etag)

    def test_a_changed_payload_gets_a_different_tag(self) -> None:
        changed = {**PAYLOAD, 'repository_ids': ['client', 'backend']}
        self.assertNotEqual(
            TaggedPayload.from_payload(PAYLOAD).etag,
            TaggedPayload.from_payload(changed).etag,
        )

    def test_repeated_content_compresses_well(self) -> None:
        # Diffs and trees are mostly repeated context lines and directory
        # names. If an encoding change ever destroyed that, compressing would
        # stop being worth its cost and this would say so.
        tagged = TaggedPayload.from_payload(PAYLOAD)
        self.assertLess(len(tagged.gzipped) * 4, len(tagged.body))


class ConditionalResponseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = Flask(__name__)
        self.tagged = TaggedPayload.from_payload(PAYLOAD)

    def _respond(self, headers=None, extra=None):
        with self.app.test_request_context(headers=headers or {}) as ctx:
            return conditional_json_response(
                self.app, ctx.request, self.tagged, headers=extra,
            )

    def test_a_first_request_gets_the_whole_payload_and_a_tag(self) -> None:
        response = self._respond()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers['ETag'])
        self.assertEqual(json.loads(response.get_data()), PAYLOAD)

    def test_a_matching_tag_is_an_empty_304(self) -> None:
        response = self._respond({'If-None-Match': f'"{self.tagged.etag}"'})
        self.assertEqual(response.status_code, 304)
        self.assertEqual(response.get_data(), b'')

    def test_a_stale_tag_gets_the_whole_payload(self) -> None:
        response = self._respond({'If-None-Match': '"not-this-one"'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.get_data()), PAYLOAD)

    def test_gzip_is_used_when_the_client_accepts_it(self) -> None:
        response = self._respond({'Accept-Encoding': 'gzip'})
        self.assertEqual(response.headers['Content-Encoding'], 'gzip')
        # A cache between here and the browser must key on the encoding.
        self.assertEqual(response.headers['Vary'], 'Accept-Encoding')
        self.assertEqual(json.loads(gzip.decompress(response.get_data())), PAYLOAD)

    def test_plain_json_when_gzip_is_not_accepted(self) -> None:
        response = self._respond({'Accept-Encoding': 'identity'})
        self.assertIsNone(response.headers.get('Content-Encoding'))
        self.assertEqual(json.loads(response.get_data()), PAYLOAD)

    def test_the_answer_is_never_cached_by_the_browser(self) -> None:
        # The tag decides freshness; a browser cache holding the body would
        # answer without ever asking, and the pane would go stale.
        self.assertEqual(self._respond().headers['Cache-Control'], 'no-cache')

    def test_extra_headers_reach_a_200(self) -> None:
        response = self._respond(extra={'X-Tree-Cache': 'hit'})
        self.assertEqual(response.headers['X-Tree-Cache'], 'hit')

    def test_extra_headers_reach_a_304_too(self) -> None:
        # A header describing the ANSWER (not the payload) still has to reach
        # a client that is being told nothing changed.
        response = self._respond(
            {'If-None-Match': f'"{self.tagged.etag}"'}, extra={'X-Tree-Cache': 'hit'},
        )
        self.assertEqual(response.status_code, 304)
        self.assertEqual(response.headers['X-Tree-Cache'], 'hit')

    def test_a_gzipped_answer_carries_the_same_tag_as_a_plain_one(self) -> None:
        # Otherwise a client that switches encodings re-downloads everything.
        plain = self._respond({'Accept-Encoding': 'identity'})
        zipped = self._respond({'Accept-Encoding': 'gzip'})
        self.assertEqual(plain.headers['ETag'], zipped.headers['ETag'])


class ResponseCompressionTests(unittest.TestCase):
    """kato serves its own static files, so nothing else will compress them.

    The UI bundle went out UNCOMPRESSED on every cold load: 5.3 MB where 1.5 MB
    would do, the single largest thing the operator waits for.
    """

    BODY = (b'console.log("kato");' * 200)

    def _client(self, *, body=None, mimetype='application/javascript',
                etag='"v1"', encoding='', streamed=False):
        app = Flask(__name__)
        register_response_compression(app)
        payload = self.BODY if body is None else body

        @app.get('/asset')
        def asset():  # noqa: WPS430 - Flask needs the closure
            if streamed:
                response = app.response_class(
                    (chunk for chunk in (payload,)), mimetype=mimetype,
                )
            else:
                response = app.response_class(payload, mimetype=mimetype)
            if etag:
                response.headers['ETag'] = etag
            if encoding:
                response.headers['Content-Encoding'] = encoding
            return response

        return app.test_client()

    @staticmethod
    def _get(client, accept='gzip'):
        headers = {'Accept-Encoding': accept} if accept else {}
        return client.get('/asset', headers=headers)

    def test_a_text_asset_is_compressed_for_a_client_that_asked(self) -> None:
        response = self._get(self._client())

        self.assertEqual(response.headers['Content-Encoding'], 'gzip')
        self.assertEqual(response.headers['Vary'], 'Accept-Encoding')
        self.assertLess(len(response.get_data()), len(self.BODY))

    def test_it_decompresses_to_exactly_what_was_served(self) -> None:
        response = self._get(self._client())

        self.assertEqual(gzip.decompress(response.get_data()), self.BODY)

    def test_the_declared_length_matches_the_bytes_sent(self) -> None:
        # A stale Content-Length truncates the asset in the browser.
        response = self._get(self._client())

        self.assertEqual(
            int(response.headers['Content-Length']), len(response.get_data()),
        )

    def test_a_client_that_does_not_ask_gets_the_plain_bytes(self) -> None:
        response = self._get(self._client(), accept='identity')

        self.assertIsNone(response.headers.get('Content-Encoding'))
        self.assertEqual(response.get_data(), self.BODY)

    def test_a_request_with_no_accept_encoding_is_left_alone(self) -> None:
        response = self._get(self._client(), accept='')

        self.assertIsNone(response.headers.get('Content-Encoding'))

    def test_an_already_encoded_response_is_not_double_compressed(self) -> None:
        # The tree and diff routes compress themselves; encoding twice hands
        # the browser garbage.
        response = self._get(self._client(encoding='gzip'))

        self.assertEqual(response.headers['Content-Encoding'], 'gzip')
        self.assertEqual(response.get_data(), self.BODY)

    def test_a_streamed_response_is_never_touched(self) -> None:
        # The chat's event stream has to arrive frame by frame.
        response = self._get(self._client(streamed=True))

        self.assertIsNone(response.headers.get('Content-Encoding'))

    def test_an_image_is_left_alone(self) -> None:
        response = self._get(self._client(mimetype='image/png'))

        self.assertIsNone(response.headers.get('Content-Encoding'))

    def test_a_tiny_body_is_left_alone(self) -> None:
        # Under the threshold gzip's framing costs more than it saves.
        response = self._get(self._client(body=b'ok'))

        self.assertIsNone(response.headers.get('Content-Encoding'))

    def test_the_bundle_is_compressed_ONCE_per_version(self) -> None:
        # ~66ms for a 5 MB bundle: doing that per page load would trade bytes
        # for latency, which is the opposite of the point.
        client = self._client()
        with patch.object(
            http_payload.gzip, 'compress', wraps=http_payload.gzip.compress,
        ) as compress:
            first = self._get(client)
            second = self._get(client)

        self.assertEqual(compress.call_count, 1)
        self.assertEqual(first.get_data(), second.get_data())

    def test_a_rebuilt_asset_is_compressed_again(self) -> None:
        # The ETag encodes mtime + size, so a new build misses the cache.
        app = Flask(__name__)
        register_response_compression(app)
        state = {'etag': '"v1"', 'body': self.BODY}

        @app.get('/asset')
        def asset():  # noqa: WPS430 - Flask needs the closure
            response = app.response_class(
                state['body'], mimetype='application/javascript',
            )
            response.headers['ETag'] = state['etag']
            return response

        client = app.test_client()
        self._get(client)
        state['etag'] = '"v2"'
        state['body'] = self.BODY + b'// rebuilt\n'

        response = self._get(client)

        self.assertEqual(gzip.decompress(response.get_data()), state['body'])


class StaticFileCompressionTests(unittest.TestCase):
    """The case every test above could NOT catch.

    Flask hands a static file back as a FILE WRAPPER, and a file wrapper has no
    length — so werkzeug reports it as "streamed" even though Content-Length is
    set. A guard that skipped every streamed response therefore skipped every
    static file, which is the 5.3 MB bundle this whole feature exists for,
    while the tests above still passed because they build a response from
    BYTES. Measured before this test existed: 0% cut, no encoding header.
    """

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        static = Path(tmp.name)
        self.body = b'console.log("kato");' * 500
        (static / 'app.js').write_bytes(self.body)
        # ``static_url_path`` is pinned: Flask derives it from the static
        # folder's BASENAME, so a temp directory would mount at /tmpXXXX and
        # every request here would 404 — a harness bug that reads exactly like
        # the feature being broken.
        app = Flask(
            __name__, static_folder=str(static), static_url_path='/static',
        )
        register_response_compression(app)
        self.client = app.test_client()

    def _get(self, **headers):
        return self.client.get('/static/app.js', headers=headers)

    def test_a_static_file_is_compressed(self) -> None:
        response = self._get(**{'Accept-Encoding': 'gzip'})

        self.assertEqual(response.headers['Content-Encoding'], 'gzip')
        self.assertLess(len(response.get_data()), len(self.body))

    def test_it_decompresses_to_the_file_on_disk(self) -> None:
        response = self._get(**{'Accept-Encoding': 'gzip'})

        self.assertEqual(gzip.decompress(response.get_data()), self.body)

    def test_a_client_that_does_not_ask_gets_the_file_unchanged(self) -> None:
        response = self._get(**{'Accept-Encoding': 'identity'})

        self.assertIsNone(response.headers.get('Content-Encoding'))
        self.assertEqual(response.get_data(), self.body)

    def test_the_conditional_304_still_works(self) -> None:
        # Compression must not cost the revalidation that makes repeat loads
        # cheap: a 304 has no body to compress and must stay empty.
        first = self._get(**{'Accept-Encoding': 'gzip'})

        again = self._get(**{
            'Accept-Encoding': 'gzip', 'If-None-Match': first.headers['ETag'],
        })

        self.assertEqual(again.status_code, 304)
        self.assertEqual(again.get_data(), b'')


if __name__ == '__main__':
    unittest.main()

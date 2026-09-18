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
import unittest

from flask import Flask

from kato_webserver.http_payload import (
    TaggedPayload,
    conditional_json_response,
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


if __name__ == '__main__':
    unittest.main()

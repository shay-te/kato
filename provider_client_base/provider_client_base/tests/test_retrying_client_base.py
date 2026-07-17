import unittest

from provider_client_base.provider_client_base.retrying_client_base import (
    RetryingClientBase,
)


class AbsUrlTests(unittest.TestCase):
    """Regression: Bitbucket's pagination ``next`` field hands back an
    already-fully-qualified URL for the next page, not a relative path.
    Blindly prepending ``base_url`` produced a doubled, always-404ing
    URL (``https://api.bitbucket.org/2.0/https://api.bitbucket.org/2.0/
    ...``) — silently losing every PR comment past page 1, on every
    scan cycle, for every repo. Existing pagination tests mocked
    ``_get`` directly and never exercised the real ``_abs_url``, which
    is why this shipped unnoticed.
    """

    def setUp(self) -> None:
        self.client = RetryingClientBase(
            'https://api.bitbucket.org/2.0', 'token', timeout=10,
        )

    def test_relative_path_gets_base_url_prepended(self) -> None:
        self.assertEqual(
            self.client._abs_url('/repositories/foo/bar'),
            'https://api.bitbucket.org/2.0/repositories/foo/bar',
        )

    def test_relative_path_without_leading_slash(self) -> None:
        self.assertEqual(
            self.client._abs_url('repositories/foo/bar'),
            'https://api.bitbucket.org/2.0/repositories/foo/bar',
        )

    def test_absolute_https_url_passed_through_unchanged(self) -> None:
        absolute = (
            'https://api.bitbucket.org/2.0/repositories/foo/bar/'
            'pullrequests/1/comments?page=2'
        )
        self.assertEqual(self.client._abs_url(absolute), absolute)

    def test_absolute_url_on_a_different_host_is_still_passed_through(self) -> None:
        # Pagination links are provider-controlled; the point is "don't
        # re-prefix an absolute URL", not "only trust our own host".
        absolute = 'https://bitbucket.example/page2'
        self.assertEqual(self.client._abs_url(absolute), absolute)

    def test_absolute_http_url_passed_through_unchanged(self) -> None:
        absolute = 'http://internal.example/repositories/foo/bar'
        self.assertEqual(self.client._abs_url(absolute), absolute)


class _RecordingSession(object):
    """A real (non-mock) stand-in for ``requests.Session`` that records the
    URL each verb is called with, so we can prove the verb built its URL
    through ``_abs_url``."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def _record(self, verb: str):
        def _call(url, *args, **kwargs):
            self.calls.append((verb, url))
            return url
        return _call

    def __getattr__(self, name: str):
        if name in ('get', 'post', 'put', 'patch', 'delete'):
            return self._record(name)
        raise AttributeError(name)


class AllVerbsUseAbsUrlTests(unittest.TestCase):
    """The real bug behind ``_abs_url``: only ``_patch`` used it, while
    ``_get``/``_post``/``_put``/``_delete`` inherited ``ClientBase``'s builder
    (no absolute-URL check) — so the verbs that actually paginate doubled an
    absolute ``next`` URL and 404'd. Every verb must now route through
    ``_abs_url``."""

    def setUp(self) -> None:
        self.client = RetryingClientBase(
            'https://api.bitbucket.org/2.0', 'token', timeout=10,
        )
        self.session = _RecordingSession()
        self.client.session = self.session

    def test_every_verb_passes_an_absolute_url_through_without_doubling(self) -> None:
        absolute = (
            'https://api.bitbucket.org/2.0/repositories/foo/bar/'
            'pullrequests/1/comments?page=TOKEN2'
        )
        self.client._get(absolute)
        self.client._post(absolute)
        self.client._put(absolute)
        self.client._patch(absolute)
        self.client._delete(absolute)
        self.assertEqual(
            self.session.calls,
            [
                ('get', absolute), ('post', absolute), ('put', absolute),
                ('patch', absolute), ('delete', absolute),
            ],
        )

    def test_every_verb_prepends_base_url_for_a_relative_path(self) -> None:
        expected = 'https://api.bitbucket.org/2.0/repositories/foo/bar'
        for verb in ('_get', '_post', '_put', '_patch', '_delete'):
            getattr(self.client, verb)('/repositories/foo/bar')
        self.assertEqual({url for _, url in self.session.calls}, {expected})


if __name__ == '__main__':
    unittest.main()

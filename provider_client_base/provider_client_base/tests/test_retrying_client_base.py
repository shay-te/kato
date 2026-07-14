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


if __name__ == '__main__':
    unittest.main()

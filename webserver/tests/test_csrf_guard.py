"""Coverage for the cross-origin request guard in ``kato_webserver.app``.

Kato ships with no login/session, so the threat this guard defends
against isn't a stolen auth cookie — it's a page open in the operator's
own browser using that browser as a network pivot to reach the
loopback-bound API (``fetch('http://127.0.0.1:5050/api/...')`` from any
site the operator happens to visit). Origin/Referer validation closes
that gap without needing any auth — and GET is guarded too: an audit
found real GET-triggered mutations in this app (merge-finalize commits,
comment-run dispatch, permission auto-resolve), reachable via nothing
more than ``<img src="...">`` on any page. Browsers DO send ``Referer``
on a cross-origin ``<img>`` GET even though they skip ``Origin`` for
it, so the guard still catches that vector.
"""

from __future__ import annotations

import unittest

from kato_webserver.app import create_app


class _FakeManager:
    def get_session(self, task_id):  # noqa: ARG002
        return None

    def list_records(self):
        return []


class CsrfGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app(session_manager=_FakeManager())
        self.client = self.app.test_client()

    def test_get_with_no_origin_or_referer_is_allowed(self) -> None:
        # Direct navigation (typed URL, bookmark), curl, and other
        # non-browser clients carry no ambient browser context —
        # nothing for this guard to protect here.
        response = self.client.get('/api/sessions')
        self.assertNotEqual(response.status_code, 403)

    def test_get_with_matching_origin_is_allowed(self) -> None:
        response = self.client.get(
            '/api/sessions',
            headers={'Origin': 'http://localhost'},
            environ_overrides={'HTTP_HOST': 'localhost'},
        )
        self.assertNotEqual(response.status_code, 403)

    def test_get_with_cross_origin_origin_header_is_rejected(self) -> None:
        response = self.client.get(
            '/api/sessions',
            headers={'Origin': 'https://evil.example'},
        )
        self.assertEqual(response.status_code, 403)

    def test_get_with_cross_origin_referer_is_rejected(self) -> None:
        # The <img src="..."> vector: no Origin header, but the browser
        # DOES send Referer — this is the exact case that motivated
        # guarding GET at all (merge-finalize / permission auto-resolve
        # are real GET-triggered mutations in this app).
        response = self.client.get(
            '/api/sessions',
            headers={'Referer': 'https://evil.example/attack.html'},
        )
        self.assertEqual(response.status_code, 403)

    def test_get_with_matching_referer_is_allowed(self) -> None:
        response = self.client.get(
            '/api/sessions',
            headers={'Referer': 'http://localhost/some/page'},
            environ_overrides={'HTTP_HOST': 'localhost'},
        )
        self.assertNotEqual(response.status_code, 403)

    def test_options_is_still_exempt(self) -> None:
        # OPTIONS never runs a view function (Flask/Werkzeug answers it
        # automatically), so there's no route body — and no side
        # effect — for a forged one to reach.
        response = self.client.open(
            '/api/sessions', method='OPTIONS',
            headers={'Origin': 'https://evil.example'},
        )
        self.assertNotEqual(response.status_code, 403)

    def test_post_with_no_origin_or_referer_is_allowed(self) -> None:
        # A non-browser client (curl, the desktop shell's own HTTP
        # client, server-to-server calls) carries no ambient browser
        # context to abuse — nothing for this guard to protect here.
        response = self.client.post('/api/sessions/T-1/permission', json={})
        self.assertNotEqual(response.status_code, 403)

    def test_post_with_matching_origin_is_allowed(self) -> None:
        response = self.client.post(
            '/api/sessions/T-1/permission',
            json={},
            headers={'Origin': 'http://localhost'},
            environ_overrides={'HTTP_HOST': 'localhost'},
        )
        self.assertNotEqual(response.status_code, 403)

    def test_post_with_cross_origin_origin_header_is_rejected(self) -> None:
        response = self.client.post(
            '/api/sessions/T-1/permission',
            json={},
            headers={'Origin': 'https://evil.example'},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn('cross-origin', response.get_json().get('error', ''))

    def test_post_with_cross_origin_referer_is_rejected_when_origin_absent(self) -> None:
        response = self.client.post(
            '/api/sessions/T-1/permission',
            json={},
            headers={'Referer': 'https://evil.example/attack.html'},
        )
        self.assertEqual(response.status_code, 403)

    def test_matching_referer_is_allowed_when_origin_absent(self) -> None:
        response = self.client.post(
            '/api/sessions/T-1/permission',
            json={},
            headers={'Referer': 'http://localhost/some/page'},
            environ_overrides={'HTTP_HOST': 'localhost'},
        )
        self.assertNotEqual(response.status_code, 403)

    def test_origin_takes_precedence_over_referer(self) -> None:
        # A same-origin Referer must not paper over a cross-origin Origin.
        response = self.client.post(
            '/api/sessions/T-1/permission',
            json={},
            headers={
                'Origin': 'https://evil.example',
                'Referer': 'http://localhost/some/page',
            },
            environ_overrides={'HTTP_HOST': 'localhost'},
        )
        self.assertEqual(response.status_code, 403)

    def test_delete_is_also_guarded(self) -> None:
        response = self.client.delete(
            '/api/sessions/T-1/comments/1',
            headers={'Origin': 'https://evil.example'},
        )
        self.assertEqual(response.status_code, 403)

    def test_origin_null_is_rejected_not_treated_as_absent(self) -> None:
        # Regression: ``Origin: null`` is what a real browser sends from an
        # opaque-origin context (a sandboxed iframe without
        # allow-same-origin, a data: URI) — the textbook null-origin CSRF
        # bypass. urlsplit('null').netloc == '', which the old blocklist
        # compare (``origin_host and origin_host != request.host``)
        # silently treated identically to "no header sent at all" and let
        # through. A header that WAS sent but doesn't resolve to a real
        # host is exactly the suspicious case, not the safe one.
        response = self.client.post(
            '/api/sessions/T-1/permission',
            json={},
            headers={'Origin': 'null'},
        )
        self.assertEqual(response.status_code, 403)

    def test_referer_null_is_rejected_not_treated_as_absent(self) -> None:
        response = self.client.get(
            '/api/sessions',
            headers={'Referer': 'null'},
        )
        self.assertEqual(response.status_code, 403)


class FetchMetadataGuardTests(unittest.TestCase):
    """``Sec-Fetch-Site`` — the half the Origin/Referer pair cannot cover.

    The Origin/Referer check is defeated by the page it defends against: the
    REQUESTING page picks the referrer policy, so ``referrerpolicy=
    "no-referrer"`` on an ``<img>``, a document-wide ``<meta name="referrer">``,
    or a ``no-cors`` fetch all arrive with neither header and hit the
    deliberate "no ambient browser context" bail.

    ``Sec-Fetch-Site`` is a forbidden header name — set by the browser, not
    forgeable or clearable by script, and unaffected by referrer policy — so
    it still tells the truth about those requests.
    """

    def setUp(self) -> None:
        self.app = create_app(session_manager=_FakeManager())
        self.client = self.app.test_client()

    def test_cross_site_is_rejected_with_no_origin_and_no_referer(self) -> None:
        # THE BUG. `<img src="http://127.0.0.1:5050/api/sessions"
        # referrerpolicy="no-referrer">` on any page the operator visits.
        # Before the fetch-metadata check this reached the handler, and
        # GET /api/sessions can auto-resolve a live agent's pending
        # tool-permission ask.
        response = self.client.get(
            '/api/sessions',
            headers={'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Dest': 'image'},
        )
        self.assertEqual(response.status_code, 403)

    def test_same_site_is_rejected_too(self) -> None:
        # Another port on the same host is same-SITE but cross-ORIGIN, and the
        # Origin compare (per-netloc) rejects it. This must not be looser than
        # the check it backs up.
        response = self.client.get(
            '/api/sessions',
            headers={'Sec-Fetch-Site': 'same-site'},
        )
        self.assertEqual(response.status_code, 403)

    def test_same_origin_is_allowed(self) -> None:
        # The UI's own fetch() calls.
        response = self.client.get(
            '/api/sessions',
            headers={'Sec-Fetch-Site': 'same-origin'},
        )
        self.assertNotEqual(response.status_code, 403)

    def test_none_is_allowed(self) -> None:
        # A user-initiated navigation — typed URL or bookmark.
        response = self.client.get(
            '/',
            headers={'Sec-Fetch-Site': 'none', 'Sec-Fetch-Mode': 'navigate'},
        )
        self.assertNotEqual(response.status_code, 403)

    def test_a_client_that_sends_no_fetch_metadata_is_unaffected(self) -> None:
        # curl, the /healthz probe, server-to-server. The deliberate
        # no-ambient-context exemption must survive this change intact —
        # that is what keeps kato scriptable from a local shell.
        response = self.client.get('/api/sessions')
        self.assertNotEqual(response.status_code, 403)

    def test_cross_site_is_rejected_on_post_too(self) -> None:
        response = self.client.post(
            '/api/scan',
            json={},
            headers={'Sec-Fetch-Site': 'cross-site'},
        )
        self.assertEqual(response.status_code, 403)

    def test_options_stays_exempt(self) -> None:
        # The preflight must not be answered with a 403, or the real request
        # never happens.
        response = self.client.open(
            '/api/sessions',
            method='OPTIONS',
            headers={'Sec-Fetch-Site': 'cross-site'},
        )
        self.assertNotEqual(response.status_code, 403)

    def test_a_forged_same_origin_still_faces_the_origin_check(self) -> None:
        # Belt and braces: the fetch-metadata check runs FIRST and does not
        # replace the Origin compare. A non-browser client can send anything,
        # so a hand-set 'same-origin' must not buy a pass past a mismatched
        # Origin.
        response = self.client.get(
            '/api/sessions',
            headers={
                'Sec-Fetch-Site': 'same-origin',
                'Origin': 'https://evil.example',
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_an_unknown_future_value_is_rejected(self) -> None:
        # Allowlist, not blocklist — the same reasoning that made
        # ``Origin: null`` a rejection rather than a silent pass.
        response = self.client.get(
            '/api/sessions',
            headers={'Sec-Fetch-Site': 'some-future-value'},
        )
        self.assertEqual(response.status_code, 403)

    def test_the_guarded_get_routes_are_actually_covered(self) -> None:
        # The three GET routes with real side effects, named in the guard's
        # own docstring. Each must 403 on the suppressed-header vector.
        for path in (
            '/api/sessions',
            '/api/permissions/pending',
            '/api/sessions/T1/files',
            '/api/sessions/T1/diff',
        ):
            with self.subTest(path=path):
                response = self.client.get(
                    path, headers={'Sec-Fetch-Site': 'cross-site'},
                )
                self.assertEqual(response.status_code, 403)

class PageNavigationCarveOutTests(unittest.TestCase):
    """A cross-site TOP-LEVEL navigation to a PAGE is allowed; to /api is not.

    The Tauri shell points its webview at ``http://localhost:<port>/?_=<nonce>``
    with ``WebviewUrl::External``. That is an embedder-initiated navigation and
    the platform webviews are not obliged to label it ``none``; if one reports
    ``cross-site``, a strict check answers the desktop app's first request with
    a 403 and the operator sees an error page instead of kato.

    Allowing it costs nothing: a navigation loads a document the initiating
    page cannot read. The vector the guard exists to stop is the SUBRESOURCE
    one, which is never ``Sec-Fetch-Mode: navigate``.
    """

    def setUp(self) -> None:
        self.app = create_app(session_manager=_FakeManager())
        self.client = self.app.test_client()

    def test_a_cross_site_navigation_to_the_app_shell_is_allowed(self) -> None:
        response = self.client.get(
            '/',
            headers={
                'Sec-Fetch-Site': 'cross-site',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Dest': 'document',
            },
        )
        self.assertNotEqual(response.status_code, 403)

    def test_the_desktop_shell_cache_busted_url_is_allowed(self) -> None:
        # The exact shape main.rs navigates to.
        response = self.client.get(
            '/?_=abc123',
            headers={'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate'},
        )
        self.assertNotEqual(response.status_code, 403)

    def test_a_navigation_to_an_api_route_is_STILL_rejected(self) -> None:
        # /api/* has real side effects; a navigation to one is noisy but
        # pointless to permit.
        response = self.client.get(
            '/api/sessions',
            headers={
                'Sec-Fetch-Site': 'cross-site',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Dest': 'document',
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_a_subresource_load_of_a_page_route_is_still_rejected(self) -> None:
        # Same path, but fetched as an <img>/script rather than navigated to —
        # the carve-out must key on the MODE, not the path alone.
        response = self.client.get(
            '/',
            headers={
                'Sec-Fetch-Site': 'cross-site',
                'Sec-Fetch-Mode': 'no-cors',
                'Sec-Fetch-Dest': 'image',
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_a_cross_origin_referer_on_a_navigation_still_wins(self) -> None:
        # The carve-out only skips the FETCH-METADATA check. The Origin/Referer
        # compare below it is untouched, and still rejects.
        response = self.client.get(
            '/',
            headers={
                'Sec-Fetch-Site': 'cross-site',
                'Sec-Fetch-Mode': 'navigate',
                'Referer': 'https://evil.example/',
            },
        )
        self.assertEqual(response.status_code, 403)



if __name__ == '__main__':
    unittest.main()

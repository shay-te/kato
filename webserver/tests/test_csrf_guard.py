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


if __name__ == '__main__':
    unittest.main()

"""End-to-end flow tests for the GitLab clients — A-Z scenarios.

Each test class represents one named flow and drives a public method call
straight down through the *real* call chain — the retry wrapper, the
absolute-URL builder, and the header/timeout merge — stopping only at the
lowest-level HTTP boundary: the underlying ``requests.Session``. Only
``session.get`` / ``session.post`` / ``session.put`` are intercepted, so
the full parsing, filtering, and assembly logic runs and the asserted
endpoints + payloads are exactly what would hit the wire.

These tests are self-contained: they depend on nothing outside this lib
and its sanctioned ``provider_client_base`` base, and use only generic
example project / user / merge-request data.
"""
from __future__ import annotations

import unittest
from unittest.mock import Mock

from provider_client_base.provider_client_base.data.fields import (
    PullRequestFields,
    ReviewCommentFields,
)
from provider_client_base.provider_client_base.data.issue_record import IssueRecord
from provider_client_base.provider_client_base.data.review_comment import ReviewComment

from gitlab_core_lib.gitlab_core_lib.client.gitlab_client import GitLabClient
from gitlab_core_lib.gitlab_core_lib.client.gitlab_issues_client import (
    GitLabIssuesClient,
)
from gitlab_core_lib.gitlab_core_lib.data.fields import (
    ISSUE_ALL_COMMENTS,
    ISSUE_COMMENT_AUTHOR,
    ISSUE_COMMENT_BODY,
)

BASE_URL = 'https://gitlab.example/api/v4'
TOKEN = 'glpat-example'
PROJECT = 'acme/web-app'
# URL-encoded form of ``acme/web-app`` (the ``/`` becomes ``%2F``).
ENCODED_PROJECT = 'acme%2Fweb-app'
TIMEOUT = 30


# ---------------------------------------------------------------------------
# Test doubles — a fake ``requests`` Response and a routed Session
# ---------------------------------------------------------------------------
def _response(*, json_data=None, status_code: int = 200, headers=None) -> Mock:
    """A stand-in for ``requests.Response`` sufficient for the client."""
    response = Mock(status_code=status_code)
    response.json.return_value = json_data
    response.headers = headers or {}
    return response


class _RoutedSession(object):
    """A fake ``requests.Session`` that dispatches by (verb, abs_url).

    Records every call so a flow can assert the exact endpoint, params,
    json body, merged headers, and timeout that reached the HTTP layer.
    """

    def __init__(self, router) -> None:
        self._router = router
        self.calls: list[dict] = []

    def _record(self, verb: str, url: str, kwargs: dict):
        self.calls.append({'verb': verb, 'url': url, 'kwargs': kwargs})
        return self._router(verb, url, kwargs)

    def get(self, url, *args, **kwargs):
        return self._record('GET', url, kwargs)

    def post(self, url, *args, **kwargs):
        return self._record('POST', url, kwargs)

    def put(self, url, *args, **kwargs):
        return self._record('PUT', url, kwargs)


def _issues_client(router, **kwargs) -> GitLabIssuesClient:
    client = GitLabIssuesClient(BASE_URL, TOKEN, PROJECT, max_retries=1, **kwargs)
    client.session = _RoutedSession(router)
    return client


def _mr_client(router, **kwargs) -> GitLabClient:
    client = GitLabClient(BASE_URL, TOKEN, max_retries=1, **kwargs)
    client.session = _RoutedSession(router)
    return client


def _url(path: str) -> str:
    """The absolute URL the client builds for ``path``."""
    return f'{BASE_URL}/{path.lstrip("/")}'


# ---------------------------------------------------------------------------
# F1 — Issues A-Z: validate -> list assigned -> comment -> label -> close
# ---------------------------------------------------------------------------
class F1_IssueLifecycle(unittest.TestCase):
    """Full issue workflow start to finish against a mocked HTTP session.

    A. construct/auth the issue client
    B. validate the connection
    C. list assigned issues (filtered by allowed state)
    D. per-issue notes folded into the description + ALL_COMMENTS
    E. labels surface as tags; branch name derived from id
    F. post a progress note
    G. move to a "review" label
    H. close the issue
    """

    def _make_router(self):
        issues_url = _url(f'/projects/{ENCODED_PROJECT}/issues')
        notes_101 = _url(f'/projects/{ENCODED_PROJECT}/issues/101/notes')
        notes_102 = _url(f'/projects/{ENCODED_PROJECT}/issues/102/notes')
        issue_101 = _url(f'/projects/{ENCODED_PROJECT}/issues/101')

        issues_payload = [
            {
                'iid': 101,
                'title': 'Cache expensive queries',
                'description': 'The dashboard re-queries on every render.',
                'state': 'opened',
                'labels': ['performance', 'backend'],
            },
            {
                'iid': 102,
                'title': 'Old archived ticket',
                'description': 'No longer relevant.',
                'state': 'closed',
                'labels': [],
            },
        ]
        notes_payload = [
            {'body': 'changed milestone', 'author': {'username': 'gitlab-bot'},
             'system': True},
            {'body': 'Please add a regression test.',
             'author': {'name': 'Dana Reviewer', 'username': 'dana'}},
        ]

        def router(verb, url, kwargs):
            if verb == 'GET' and url == issues_url:
                return _response(json_data=issues_payload)
            if verb == 'GET' and url == notes_101:
                return _response(json_data=notes_payload)
            if verb == 'GET' and url == notes_102:
                return _response(json_data=[])
            if verb == 'POST' and url == notes_101:
                return _response(json_data={'id': 5001})
            if verb == 'PUT' and url == issue_101:
                return _response(json_data={'iid': 101})
            raise AssertionError(f'unexpected {verb} {url}')

        return router

    def test_flow(self):
        client = _issues_client(self._make_router())

        # B. validate connection (one GET, raises nothing)
        client.validate_connection(PROJECT, 'maintainer-bot', ['opened'])

        # C/D/E. list assigned issues
        records = client.get_assigned_tasks(PROJECT, 'maintainer-bot', ['opened'])

        # Only the opened issue survives the state filter.
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertIsInstance(record, IssueRecord)
        self.assertEqual(record.id, '101')
        self.assertEqual(record.summary, 'Cache expensive queries')
        # D. real human note folded into description; system note dropped.
        self.assertIn('The dashboard re-queries on every render.',
                      record.description)
        self.assertIn('Dana Reviewer: Please add a regression test.',
                      record.description)
        self.assertNotIn('changed milestone', record.description)
        # ALL_COMMENTS holds only the non-system note.
        all_comments = getattr(record, ISSUE_ALL_COMMENTS)
        self.assertEqual(len(all_comments), 1)
        self.assertEqual(all_comments[0][ISSUE_COMMENT_AUTHOR], 'Dana Reviewer')
        self.assertEqual(all_comments[0][ISSUE_COMMENT_BODY],
                         'Please add a regression test.')
        # E. labels -> tags, derived branch name.
        self.assertEqual(record.tags, ['performance', 'backend'])
        self.assertEqual(record.branch_name, 'feature/101')

        # F. post a progress note.
        client.add_comment('101', 'Working on this now.')

        # G. move to a review label (labels field -> add_labels).
        client.move_issue_to_state('101', 'labels', 'In Review')

        # H. close the issue (state field -> state_event=close).
        client.move_issue_to_state('101', 'state', 'closed')

        # ---- assert the wire traffic, in order ----
        calls = client.session.calls
        # validate GET, list GET, notes-101 GET, add-comment POST,
        # label PUT, close PUT. Issue 102 is filtered out by state
        # BEFORE its record is built, so its notes are never fetched.
        self.assertEqual(
            [(c['verb']) for c in calls],
            ['GET', 'GET', 'GET', 'POST', 'PUT', 'PUT'],
        )

        # B. validate hit the issues endpoint with a 1-row probe.
        validate_call = calls[0]
        self.assertEqual(
            validate_call['url'],
            _url(f'/projects/{ENCODED_PROJECT}/issues'),
        )
        self.assertEqual(
            validate_call['kwargs']['params'],
            {'assignee_username': 'maintainer-bot', 'state': 'all', 'per_page': 1},
        )

        # C. list issues sent the documented query params.
        list_call = calls[1]
        self.assertEqual(
            list_call['kwargs']['params'],
            {
                'assignee_username': 'maintainer-bot',
                'state': 'all',
                'order_by': 'updated_at',
                'sort': 'desc',
                'per_page': 100,
            },
        )

        # F. progress note POSTed the body to the notes endpoint.
        post_call = calls[3]
        self.assertEqual(post_call['verb'], 'POST')
        self.assertEqual(
            post_call['url'],
            _url(f'/projects/{ENCODED_PROJECT}/issues/101/notes'),
        )
        self.assertEqual(post_call['kwargs']['json'], {'body': 'Working on this now.'})

        # G. label move PUT add_labels.
        label_call = calls[4]
        self.assertEqual(
            label_call['url'],
            _url(f'/projects/{ENCODED_PROJECT}/issues/101'),
        )
        self.assertEqual(label_call['kwargs']['json'], {'add_labels': 'In Review'})

        # H. close move PUT state_event.
        close_call = calls[5]
        self.assertEqual(close_call['kwargs']['json'], {'state_event': 'close'})

        # Auth + timeout were merged in by the real client at every hop.
        for call in calls:
            self.assertEqual(call['kwargs']['headers']['PRIVATE-TOKEN'], TOKEN)
            self.assertEqual(call['kwargs']['timeout'], TIMEOUT)


# ---------------------------------------------------------------------------
# F2 — Merge-request A-Z: open MR -> read notes -> reply -> resolve
# ---------------------------------------------------------------------------
class F2_MergeRequestReviewLifecycle(unittest.TestCase):
    """Full review workflow on a merge request against a mocked session.

    A. construct/auth the MR client
    B. open a merge request (normalised result)
    C. list MR discussion notes -> ReviewComment objects (inline metadata)
    D. reply to the reviewer's note in its discussion thread
    E. resolve the discussion
    """

    MR_IID = '42'

    def _make_router(self, *, discussions):
        mrs_url = _url(f'/projects/{ENCODED_PROJECT}/merge_requests')
        discussions_url = _url(
            f'/projects/{ENCODED_PROJECT}/merge_requests/{self.MR_IID}/discussions'
        )
        discussion_thread = _url(
            f'/projects/{ENCODED_PROJECT}/merge_requests/{self.MR_IID}'
            f'/discussions/thread-1'
        )
        notes_in_thread = discussion_thread + '/notes'

        created_mr = {
            'iid': 42,
            'title': 'Add caching layer',
            'web_url': 'https://gitlab.example/acme/web-app/-/merge_requests/42',
        }

        def router(verb, url, kwargs):
            if verb == 'POST' and url == mrs_url:
                return _response(json_data=created_mr)
            if verb == 'GET' and url == discussions_url:
                # Single page: no X-Next-Page header -> pagination stops.
                return _response(json_data=discussions)
            if verb == 'POST' and url == notes_in_thread:
                return _response(json_data={'id': 7002})
            if verb == 'PUT' and url == discussion_thread:
                return _response(json_data={'id': 'thread-1', 'resolved': True})
            raise AssertionError(f'unexpected {verb} {url}')

        return router

    def _discussions(self):
        return [
            {
                'id': 'thread-1',
                'resolved': False,
                'notes': [
                    {'id': 9001, 'body': 'assigned reviewer',
                     'author': {'username': 'gitlab'}, 'system': True},
                    {
                        'id': 9002,
                        'body': 'Please extract this into a helper.',
                        'author': {'username': 'reviewer-bea', 'name': 'Bea'},
                        'position': {
                            'new_path': 'app/cache.py',
                            'new_line': 88,
                            'head_sha': 'deadbeef',
                        },
                    },
                ],
            },
            {
                'id': 'thread-resolved',
                'resolved': True,
                'notes': [
                    {'id': 9100, 'body': 'old note already handled',
                     'author': {'username': 'reviewer-bea'}},
                ],
            },
        ]

    def test_flow(self):
        client = _mr_client(self._make_router(discussions=self._discussions()))

        # B. open the merge request.
        pull_request = client.create_pull_request(
            title='Add caching layer',
            source_branch='feature/caching',
            repo_owner='acme',
            repo_slug='web-app',
            destination_branch='main',
            description='Implements the dashboard cache.',
        )
        self.assertEqual(
            pull_request,
            {
                PullRequestFields.ID: '42',
                PullRequestFields.TITLE: 'Add caching layer',
                PullRequestFields.URL:
                    'https://gitlab.example/acme/web-app/-/merge_requests/42',
            },
        )

        # C. read the MR discussion notes.
        comments = client.list_pull_request_comments('acme', 'web-app', self.MR_IID)
        # Resolved thread skipped; system note skipped -> exactly one comment.
        self.assertEqual(len(comments), 1)
        comment = comments[0]
        self.assertIsInstance(comment, ReviewComment)
        self.assertEqual(comment.pull_request_id, self.MR_IID)
        self.assertEqual(comment.comment_id, '9002')
        self.assertEqual(comment.author, 'reviewer-bea')
        self.assertEqual(comment.body, 'Please extract this into a helper.')
        self.assertEqual(comment.file_path, 'app/cache.py')
        self.assertEqual(comment.line_number, 88)
        self.assertEqual(comment.line_type, 'added')
        self.assertEqual(comment.commit_sha, 'deadbeef')
        # The discussion id is carried so reply/resolve need no re-lookup.
        self.assertEqual(
            getattr(comment, ReviewCommentFields.RESOLUTION_TARGET_ID),
            'thread-1',
        )

        # D. reply to the reviewer in the same discussion thread.
        client.reply_to_review_comment(
            'acme', 'web-app', comment, 'Done — extracted into _build_cache().'
        )

        # E. resolve the discussion.
        client.resolve_review_comment('acme', 'web-app', comment)

        # ---- assert the wire traffic ----
        calls = client.session.calls
        self.assertEqual(
            [c['verb'] for c in calls],
            ['POST', 'GET', 'POST', 'PUT'],
        )

        # B. create MR payload.
        create_call = calls[0]
        self.assertEqual(
            create_call['url'],
            _url(f'/projects/{ENCODED_PROJECT}/merge_requests'),
        )
        self.assertEqual(
            create_call['kwargs']['json'],
            {
                PullRequestFields.TITLE: 'Add caching layer',
                'source_branch': 'feature/caching',
                'target_branch': 'main',
                PullRequestFields.DESCRIPTION: 'Implements the dashboard cache.',
            },
        )

        # C. discussions GET paginated with page=1.
        list_call = calls[1]
        self.assertEqual(
            list_call['url'],
            _url(f'/projects/{ENCODED_PROJECT}/merge_requests/{self.MR_IID}'
                 f'/discussions'),
        )
        self.assertEqual(list_call['kwargs']['params'], {'per_page': 100, 'page': 1})

        # D. reply POST landed a note on the thread.
        reply_call = calls[2]
        self.assertEqual(
            reply_call['url'],
            _url(f'/projects/{ENCODED_PROJECT}/merge_requests/{self.MR_IID}'
                 f'/discussions/thread-1/notes'),
        )
        self.assertEqual(
            reply_call['kwargs']['json'],
            {'body': 'Done — extracted into _build_cache().'},
        )

        # E. resolve PUT flagged the thread resolved.
        resolve_call = calls[3]
        self.assertEqual(
            resolve_call['url'],
            _url(f'/projects/{ENCODED_PROJECT}/merge_requests/{self.MR_IID}'
                 f'/discussions/thread-1'),
        )
        self.assertEqual(resolve_call['kwargs']['json'], {'resolved': True})

        # Bearer auth + timeout merged in by the real client throughout.
        for call in calls:
            self.assertEqual(
                call['kwargs']['headers']['Authorization'], f'Bearer {TOKEN}'
            )
            self.assertEqual(call['kwargs']['timeout'], TIMEOUT)

    def test_reply_resolves_discussion_id_when_not_carried(self):
        """Edge: a ReviewComment without a stored discussion id triggers a
        discussions lookup (by note id) before replying."""
        router = self._make_router(discussions=self._discussions())
        client = _mr_client(router)

        bare_comment = ReviewComment(
            pull_request_id=self.MR_IID,
            comment_id='9002',
            author='reviewer-bea',
            body='Please extract this into a helper.',
        )

        client.reply_to_review_comment(
            'acme', 'web-app', bare_comment, 'Acknowledged.'
        )

        calls = client.session.calls
        # First a GET to discover the thread, then the reply POST.
        self.assertEqual(calls[0]['verb'], 'GET')
        self.assertIn('/discussions', calls[0]['url'])
        reply_call = calls[-1]
        self.assertEqual(reply_call['verb'], 'POST')
        self.assertEqual(
            reply_call['url'],
            _url(f'/projects/{ENCODED_PROJECT}/merge_requests/{self.MR_IID}'
                 f'/discussions/thread-1/notes'),
        )
        self.assertEqual(reply_call['kwargs']['json'], {'body': 'Acknowledged.'})


# ---------------------------------------------------------------------------
# F3 — Find merge requests by source branch (idempotency / re-run guard)
# ---------------------------------------------------------------------------
class F3_FindMergeRequestsBySourceBranch(unittest.TestCase):
    """Flow: before opening a new MR, find an existing open one for the branch."""

    def test_flow(self):
        mrs_url = _url(f'/projects/{ENCODED_PROJECT}/merge_requests')
        payload = [
            {
                'iid': 42,
                'title': 'Add caching layer',
                'web_url': 'https://gitlab.example/acme/web-app/-/merge_requests/42',
                'source_branch': 'feature/caching',
            },
            {
                'iid': 7,
                'title': 'Unrelated change',
                'web_url': 'https://gitlab.example/acme/web-app/-/merge_requests/7',
                'source_branch': 'feature/other',
            },
        ]

        def router(verb, url, kwargs):
            if verb == 'GET' and url == mrs_url:
                return _response(json_data=payload)
            raise AssertionError(f'unexpected {verb} {url}')

        client = _mr_client(router)
        matches = client.find_pull_requests(
            'acme', 'web-app', source_branch='feature/caching'
        )

        self.assertEqual(
            matches,
            [
                {
                    PullRequestFields.ID: '42',
                    PullRequestFields.TITLE: 'Add caching layer',
                    PullRequestFields.URL:
                        'https://gitlab.example/acme/web-app/-/merge_requests/42',
                }
            ],
        )
        # The query restricts to opened MRs on the requested branch.
        params = client.session.calls[0]['kwargs']['params']
        self.assertEqual(params['state'], 'opened')
        self.assertEqual(params['source_branch'], 'feature/caching')


# ---------------------------------------------------------------------------
# F4 — Empty assigned-issue list (clean no-op path)
# ---------------------------------------------------------------------------
class F4_NoAssignedIssues(unittest.TestCase):
    """Edge: an assignee with no matching issues yields an empty list and
    makes exactly one list call (no per-issue note fetches)."""

    def test_flow(self):
        issues_url = _url(f'/projects/{ENCODED_PROJECT}/issues')

        def router(verb, url, kwargs):
            if verb == 'GET' and url == issues_url:
                return _response(json_data=[])
            raise AssertionError(f'unexpected {verb} {url}')

        client = _issues_client(router)
        records = client.get_assigned_tasks(PROJECT, 'maintainer-bot', ['opened'])

        self.assertEqual(records, [])
        self.assertEqual(len(client.session.calls), 1)


if __name__ == '__main__':
    unittest.main()

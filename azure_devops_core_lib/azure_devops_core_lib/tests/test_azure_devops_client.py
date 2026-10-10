"""The Azure Repos client, method by method, against a routed fake session."""

from __future__ import annotations

import base64
import unittest

from requests import HTTPError

from provider_client_base.provider_client_base.data.fields import ReviewCommentFields
from provider_client_base.provider_client_base.data.review_comment import ReviewComment

from azure_devops_core_lib.azure_devops_core_lib.client.auth import pat_basic_auth_header
from azure_devops_core_lib.azure_devops_core_lib.client.azure_devops_client import (
    MAX_DESCRIPTION_CHARS,
    AzureDevOpsClient,
)
from azure_devops_core_lib.azure_devops_core_lib.tests.fakes import (
    ALICE_ID,
    BOT_ID,
    OWNER,
    REPO_PATH,
    REVIEWER_ID,
    SLUG,
    STRANGER_ID,
    FakeResponse,
    client,
    comment,
    identity,
    pull_request,
    thread,
)

PR_PATH = f'{REPO_PATH}/pullrequests/42'


class AuthTests(unittest.TestCase):

    def test_a_pat_is_sent_as_basic_with_an_empty_user_name(self) -> None:
        # Bearer is for Entra ID tokens only; a PAT sent that way is a 401.
        azure = client({('GET', REPO_PATH): FakeResponse({})})
        azure.validate_connection(OWNER, SLUG)
        self.assertEqual(
            azure.session.calls[0]['kwargs']['headers'],
            {'Authorization': 'Basic ' + base64.b64encode(b':example-pat').decode('ascii')},
        )
        self.assertEqual(azure.session.calls[0]['kwargs']['timeout'], 30)

    def test_an_empty_token_still_builds_a_header(self) -> None:
        self.assertEqual(pat_basic_auth_header(''), 'Basic ' + base64.b64encode(b':').decode('ascii'))


class PathTests(unittest.TestCase):

    def test_each_segment_is_quoted_and_the_slashes_kept(self) -> None:
        azure = client({('GET', REPO_PATH): FakeResponse({})})
        azure.validate_connection(OWNER, SLUG)
        self.assertEqual(azure.session.paths(), [REPO_PATH])
        self.assertEqual(azure.session.calls[0]['kwargs']['params'], {'api-version': '7.1'})

    def test_an_on_prem_owner_keeps_its_collection_and_virtual_directory(self) -> None:
        path = '/tfs/Coll/proj/_apis/git/repositories/api'
        azure = client({('GET', path): FakeResponse({})})
        azure.validate_connection('tfs/Coll/proj', 'api')
        self.assertEqual(azure.session.paths(), [path])


class ValidateConnectionTests(unittest.TestCase):

    def test_the_refusal_carries_azures_own_sentence(self) -> None:
        azure = client({('GET', REPO_PATH): FakeResponse(
            {'message': 'TF200016: The following project does not exist: Web App.'},
            status_code=404,
        )})
        with self.assertRaisesRegex(HTTPError, 'TF200016'):
            azure.validate_connection(OWNER, SLUG)


class CreatePullRequestTests(unittest.TestCase):

    def _created(self, kwargs) -> FakeResponse:
        self.sent = kwargs['json']
        return FakeResponse(pull_request())

    def test_it_opens_the_pull_request_and_returns_the_web_page(self) -> None:
        azure = client({('POST', f'{REPO_PATH}/pullrequests'): self._created})
        result = azure.create_pull_request('UNA-1 Fix login', 'UNA-1', OWNER, SLUG, 'main', 'body')
        self.assertEqual(self.sent, {
            'sourceRefName': 'refs/heads/UNA-1',
            'targetRefName': 'refs/heads/main',
            'title': 'UNA-1 Fix login',
            'description': 'body',
        })
        self.assertEqual(result, {
            'id': '42',
            'title': 'UNA-1 Fix login',
            'url': 'https://dev.azure.com/acme/Web%20App/_git/api/pullrequest/42',
        })

    def test_no_destination_means_the_repositorys_default_branch(self) -> None:
        azure = client({
            ('GET', REPO_PATH): FakeResponse({'defaultBranch': 'refs/heads/develop'}),
            ('POST', f'{REPO_PATH}/pullrequests'): self._created,
        })
        azure.create_pull_request('t', 'UNA-1', OWNER, SLUG)
        self.assertEqual(self.sent['targetRefName'], 'refs/heads/develop')

    def test_a_repository_with_no_commits_has_nothing_to_open_into(self) -> None:
        azure = client({('GET', REPO_PATH): FakeResponse({'name': 'api'})})
        with self.assertRaisesRegex(ValueError, 'no default branch'):
            azure.create_pull_request('t', 'UNA-1', OWNER, SLUG)
        self.assertEqual(azure.session.paths('POST'), [])

    def test_a_full_ref_is_passed_as_is(self) -> None:
        azure = client({('POST', f'{REPO_PATH}/pullrequests'): self._created})
        azure.create_pull_request('t', 'refs/heads/UNA-1', OWNER, SLUG, 'refs/heads/main')
        self.assertEqual(self.sent['sourceRefName'], 'refs/heads/UNA-1')
        self.assertEqual(self.sent['targetRefName'], 'refs/heads/main')

    def test_a_long_description_is_cut_to_fit_not_refused(self) -> None:
        azure = client({('POST', f'{REPO_PATH}/pullrequests'): self._created})
        azure.create_pull_request('t', 'UNA-1', OWNER, SLUG, 'main', 'x' * 9000)
        self.assertEqual(len(self.sent['description']), MAX_DESCRIPTION_CHARS)
        self.assertTrue(self.sent['description'].endswith('(description truncated)'))

    def test_a_refusal_says_why(self) -> None:
        azure = client({('POST', f'{REPO_PATH}/pullrequests'): FakeResponse(
            {'message': 'TF401179: An active pull request for the source and target '
                        'branch already exists.'},
            status_code=409,
        )})
        with self.assertRaisesRegex(HTTPError, 'TF401179'):
            azure.create_pull_request('t', 'UNA-1', OWNER, SLUG, 'main')

    def test_a_response_without_the_repository_has_no_url(self) -> None:
        payload = pull_request()
        del payload['repository']
        azure = client({('POST', f'{REPO_PATH}/pullrequests'): FakeResponse(payload)})
        self.assertEqual(azure.create_pull_request('t', 'UNA-1', OWNER, SLUG, 'main')['url'], '')


class FindPullRequestsTests(unittest.TestCase):

    def test_active_pull_requests_of_the_branch_with_the_title_prefix(self) -> None:
        listing = FakeResponse({'value': [
            pull_request(1, 'UNA-1 Fix login'),
            pull_request(2, 'Other work'),
            pull_request(3, 'UNA-1 From another branch', sourceRefName='refs/heads/other'),
            'not a pull request',
        ]})
        azure = client({('GET', f'{REPO_PATH}/pullrequests'): listing})
        found = azure.find_pull_requests(OWNER, SLUG, source_branch='UNA-1', title_prefix='UNA-1 ')
        self.assertEqual([item['id'] for item in found], ['1'])
        self.assertEqual(azure.session.calls[0]['kwargs']['params'], {
            'api-version': '7.1',
            'searchCriteria.status': 'active',
            '$top': 100,
            'searchCriteria.sourceRefName': 'refs/heads/UNA-1',
        })

    def test_no_filters_lists_every_active_one(self) -> None:
        azure = client({('GET', f'{REPO_PATH}/pullrequests'): FakeResponse(
            {'value': [pull_request(1), pull_request(2, 'Other')]},
        )})
        self.assertEqual(len(azure.find_pull_requests(OWNER, SLUG)), 2)
        self.assertNotIn('searchCriteria.sourceRefName', azure.session.calls[0]['kwargs']['params'])

    def test_an_unexpected_payload_finds_nothing(self) -> None:
        for payload in (None, [], {'value': 'nope'}):
            with self.subTest(payload=payload):
                azure = client({('GET', f'{REPO_PATH}/pullrequests'): FakeResponse(payload)})
                self.assertEqual(azure.find_pull_requests(OWNER, SLUG), [])


class ListCommentsTests(unittest.TestCase):

    def _listed(self, *threads, **routes) -> tuple[AzureDevOpsClient, list[ReviewComment]]:
        azure = client({('GET', f'{PR_PATH}/threads'): FakeResponse({'value': list(threads)}),
                        **routes.get('extra', {})})
        return azure, azure.list_pull_request_comments(OWNER, SLUG, '42')

    def test_an_inline_comment(self) -> None:
        _, comments = self._listed(thread(
            7, comment(1, 'guard this'),
            filePath='/src/app.py', rightFileStart={'line': 12, 'offset': 1},
        ))
        self.assertEqual(len(comments), 1)
        found = comments[0]
        self.assertEqual(found.pull_request_id, '42')
        # Unique on the pull request: every thread numbers its comments from 1.
        self.assertEqual(found.comment_id, '7-1')
        self.assertEqual((found.author, found.author_id), ('alice@example.com', ALICE_ID))
        self.assertEqual((found.file_path, found.line_number, found.line_type),
                         ('src/app.py', 12, 'added'))
        self.assertEqual(getattr(found, ReviewCommentFields.RESOLUTION_TARGET_ID), '7')
        self.assertEqual(getattr(found, ReviewCommentFields.RESOLUTION_TARGET_TYPE), 'thread')
        self.assertTrue(getattr(found, ReviewCommentFields.RESOLVABLE))

    def test_a_comment_on_a_removed_line_and_a_general_one(self) -> None:
        _, comments = self._listed(
            thread(1, comment(1, 'why remove?'), filePath='/a.py', leftFileStart={'line': 3}),
            thread(2, comment(1, 'overall fine')),
        )
        self.assertEqual([(c.file_path, c.line_number, c.line_type) for c in comments],
                         [('a.py', 3, 'removed'), ('', '', '')])

    def test_two_threads_numbering_from_one_are_two_comments(self) -> None:
        _, comments = self._listed(thread(1, comment(1, 'a')), thread(2, comment(1, 'b')))
        self.assertEqual([c.comment_id for c in comments], ['1-1', '2-1'])

    def test_resolved_deleted_and_system_entries_are_left_out(self) -> None:
        _, comments = self._listed(
            thread(1, comment(1, 'open')),
            thread(2, comment(1, 'pending'), status='pending'),
            thread(3, comment(1, 'no status set'), status=None),
            *(thread(10 + i, comment(1, status), status=status)
              for i, status in enumerate(('fixed', 'wontFix', 'closed', 'byDesign'))),
            {**thread(20, comment(1, 'deleted thread')), 'isDeleted': True},
            thread(21, comment(1, 'deleted', isDeleted=True), comment(2, 'kept')),
            thread(22, comment(1, 'voted', commentType='system')),
            thread(23, {'content': 'no id', 'commentType': 'text'}, 'not a comment'),
            {'comments': [comment(1, 'thread without an id')]},
        )
        self.assertEqual([c.body for c in comments], ['open', 'pending', 'no status set', 'kept'])

    def test_a_display_name_stands_in_for_a_missing_unique_name(self) -> None:
        _, comments = self._listed(thread(1, comment(1, 'x', {'id': ALICE_ID, 'displayName': 'Alice'})))
        self.assertEqual(comments[0].author, 'Alice')

    def test_a_failed_listing_raises(self) -> None:
        azure = client({('GET', f'{PR_PATH}/threads'): FakeResponse({}, status_code=401)})
        with self.assertRaises(HTTPError):
            azure.list_pull_request_comments(OWNER, SLUG, '42')

    def test_an_unexpected_payload_lists_nothing(self) -> None:
        azure = client({('GET', f'{PR_PATH}/threads'): FakeResponse(None)})
        self.assertEqual(azure.list_pull_request_comments(OWNER, SLUG, '42'), [])


class MentionTests(unittest.TestCase):
    """``@<GUID>`` becomes a mention a filter can read: ``@{uniqueName}``."""

    def _bodies(self, content: str, *, pull_request_payload=None, connection=None) -> tuple:
        routes = {('GET', f'{PR_PATH}/threads'): FakeResponse({'value': [
            thread(1, comment(1, content)),
        ]})}
        if pull_request_payload is not None:
            routes[('GET', PR_PATH)] = pull_request_payload
        if connection is not None:
            routes[('GET', '/acme/_apis/connectionData')] = connection
        azure = client(routes)
        bodies = [c.body for c in azure.list_pull_request_comments(OWNER, SLUG, '42')]
        return azure, bodies

    def test_a_commenter_is_resolved_without_another_request(self) -> None:
        azure, bodies = self._bodies(f'@<{ALICE_ID}> agreed')
        self.assertEqual(bodies, ['@{alice@example.com} agreed'])
        self.assertEqual(azure.session.paths(), [f'{PR_PATH}/threads'])

    def test_a_reviewer_or_the_author_is_read_off_the_pull_request(self) -> None:
        payload = FakeResponse(pull_request(
            createdBy=identity(STRANGER_ID, 'dev@example.com'),
            reviewers=[identity(REVIEWER_ID, 'rev@example.com')],
        ))
        _, bodies = self._bodies(
            f'@<{REVIEWER_ID}> and @<{STRANGER_ID.lower()}>', pull_request_payload=payload,
        )
        self.assertEqual(bodies, ['@{rev@example.com} and @{dev@example.com}'])

    def test_the_bot_is_the_tokens_own_account(self) -> None:
        connection = FakeResponse({'authenticatedUser': {
            'id': BOT_ID, 'providerDisplayName': 'Review Bot',
            'properties': {'Account': {'$type': 'System.String', '$value': 'bot@example.com'}},
        }})
        _, bodies = self._bodies(
            f'@<{{{BOT_ID}}}> please fix', pull_request_payload=FakeResponse(pull_request()),
            connection=connection,
        )
        self.assertEqual(bodies, ['@{bot@example.com} please fix'])

    def test_an_unknown_identity_is_still_a_mention(self) -> None:
        _, bodies = self._bodies(
            f'@<{STRANGER_ID}> ping', pull_request_payload=FakeResponse(pull_request()),
            connection=FakeResponse({}),
        )
        self.assertEqual(bodies, [f'@{{{STRANGER_ID.lower()}}} ping'])

    def test_failed_lookups_never_cost_the_listing(self) -> None:
        _, bodies = self._bodies(
            f'@<{STRANGER_ID}> ping',
            pull_request_payload=FakeResponse({}, status_code=500),
            connection=FakeResponse({}, status_code=401),
        )
        self.assertEqual(bodies, [f'@{{{STRANGER_ID.lower()}}} ping'])

    def test_the_accounts_identity_is_read_once_per_organization(self) -> None:
        connection = FakeResponse({'authenticatedUser': {'id': BOT_ID, 'providerDisplayName': 'Bot'}})
        azure = client({
            ('GET', f'{PR_PATH}/threads'): FakeResponse({'value': [
                thread(1, comment(1, f'@<{BOT_ID}> go')),
            ]}),
            ('GET', PR_PATH): FakeResponse(pull_request()),
            ('GET', '/acme/_apis/connectionData'): connection,
        })
        for _ in range(2):
            self.assertEqual(
                [c.body for c in azure.list_pull_request_comments(OWNER, SLUG, '42')],
                ['@{Bot} go'],
            )
        self.assertEqual(azure.session.paths().count('/acme/_apis/connectionData'), 1)


class ReplyAndResolveTests(unittest.TestCase):

    @staticmethod
    def _comment(comment_id: str = '7-3', thread_id: str = '7') -> ReviewComment:
        found = ReviewComment(pull_request_id='42', comment_id=comment_id, author='a', body='b')
        if thread_id:
            setattr(found, ReviewCommentFields.RESOLUTION_TARGET_ID, thread_id)
        return found

    def test_a_reply_goes_into_the_comments_thread(self) -> None:
        azure = client({('POST', f'{PR_PATH}/threads/7/comments'): FakeResponse({})})
        azure.reply_to_review_comment(OWNER, SLUG, self._comment(), '  addressed  ')
        self.assertEqual(azure.session.calls[0]['kwargs']['json'],
                         {'content': 'addressed', 'parentCommentId': 3, 'commentType': 1})

    def test_an_id_without_a_number_replies_at_the_thread(self) -> None:
        azure = client({('POST', f'{PR_PATH}/threads/7/comments'): FakeResponse({})})
        azure.reply_to_review_comment(OWNER, SLUG, self._comment('x-y'), 'ok')
        self.assertEqual(azure.session.calls[0]['kwargs']['json']['parentCommentId'], 0)

    def test_resolving_marks_the_thread_fixed(self) -> None:
        azure = client({('PATCH', f'{PR_PATH}/threads/7'): FakeResponse({})})
        azure.resolve_review_comment(OWNER, SLUG, self._comment())
        self.assertEqual(azure.session.calls[0]['kwargs']['json'], {'status': 'fixed'})

    def test_without_its_thread_a_comment_cannot_be_answered(self) -> None:
        # Comment numbers repeat across threads: the thread cannot be found.
        azure = client()
        orphan = self._comment(thread_id='')
        for action in (lambda: azure.reply_to_review_comment(OWNER, SLUG, orphan, 'x'),
                       lambda: azure.resolve_review_comment(OWNER, SLUG, orphan)):
            with self.assertRaisesRegex(ValueError, 'thread for comment 7-3'):
                action()
        self.assertEqual(azure.session.calls, [])

    def test_a_failed_reply_raises(self) -> None:
        azure = client({('POST', f'{PR_PATH}/threads/7/comments'): FakeResponse({}, status_code=403)})
        found = self._comment()
        with self.assertRaises(HTTPError):
            azure.reply_to_review_comment(OWNER, SLUG, found, 'x')


if __name__ == '__main__':
    unittest.main()

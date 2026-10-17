"""A–Z: one pull request's life on Azure Repos, through the public client.

Open a pull request for a branch → find it again by branch and title → read
its review threads (a reviewer @-mentions the bot) → reply in the thread →
resolve it. Only the ``requests.Session`` verbs are faked (fakes.py), so the
asserted requests are exactly what would reach Azure DevOps.
"""

from __future__ import annotations

import unittest

from provider_client_base.provider_client_base.data.fields import ReviewCommentFields

from azure_devops_core_lib.azure_devops_core_lib.tests.fakes import (
    BOT_ID,
    OWNER,
    REPO_PATH,
    REVIEWER_ID,
    SLUG,
    FakeResponse,
    client,
    comment,
    identity,
    pull_request,
    thread,
)

PR_PATH = f'{REPO_PATH}/pullrequests/42'


class PullRequestLifecycleFlow(unittest.TestCase):

    def test_open_find_read_reply_resolve(self) -> None:
        reviewer = identity(REVIEWER_ID, 'rev@example.com', 'Rev')
        opened = pull_request(42, 'UNA-1 Fix login', reviewers=[reviewer])
        threads = {'value': [
            thread(
                5,
                comment(1, f'@<{BOT_ID}> this drops the error', reviewer),
                comment(2, 'Updated the code.', {'id': BOT_ID, 'uniqueName': 'bot@example.com'},
                        commentType='codeChange'),
                filePath='/src/login.py', rightFileStart={'line': 9},
            ),
            thread(6, comment(1, 'Policy: build succeeded', commentType='system')),
            thread(7, comment(1, 'already handled', reviewer), status='fixed'),
        ]}
        replies: list[dict] = []
        azure = client({
            ('GET', REPO_PATH): FakeResponse({'defaultBranch': 'refs/heads/main'}),
            ('POST', f'{REPO_PATH}/pullrequests'): FakeResponse(opened),
            ('GET', f'{REPO_PATH}/pullrequests'): FakeResponse({'value': [opened]}),
            ('GET', f'{PR_PATH}/threads'): FakeResponse(threads),
            ('POST', f'{PR_PATH}/threads/5/comments'): lambda kwargs: (
                replies.append(kwargs['json']) or FakeResponse({'id': 3})
            ),
            ('PATCH', f'{PR_PATH}/threads/5'): FakeResponse({'status': 'fixed'}),
        })

        # A. connect, B. open (onto the default branch).
        azure.validate_connection(OWNER, SLUG)
        created = azure.create_pull_request('UNA-1 Fix login', 'UNA-1', OWNER, SLUG)
        self.assertEqual(created['url'], 'https://dev.azure.com/acme/Web%20App/_git/api/pullrequest/42')

        # C. find it again the way a later pass does.
        found = azure.find_pull_requests(OWNER, SLUG, source_branch='UNA-1', title_prefix='UNA-1 ')
        self.assertEqual(found, [created])

        # D. read the review: the bot's mention is readable, the system and
        # resolved threads are gone, the bot's own reply is still listed.
        comments = azure.list_pull_request_comments(OWNER, SLUG, created['id'])
        self.assertEqual([c.comment_id for c in comments], ['5-1', '5-2'])
        asked = comments[0]
        self.assertEqual(asked.body, '@{bot@example.com} this drops the error')
        self.assertEqual((asked.file_path, asked.line_number), ('src/login.py', 9))

        # E. reply in the thread, F. resolve it.
        azure.reply_to_review_comment(OWNER, SLUG, asked, 'Fixed in 1a2b3c.')
        azure.resolve_review_comment(OWNER, SLUG, asked)
        self.assertEqual(replies, [{'content': 'Fixed in 1a2b3c.', 'parentCommentId': 1, 'commentType': 1}])
        self.assertEqual(getattr(asked, ReviewCommentFields.RESOLUTION_TARGET_ID), '5')

        self.assertEqual(
            [(call['verb'], call['path']) for call in azure.session.calls],
            [
                ('GET', REPO_PATH),
                ('GET', REPO_PATH),
                ('POST', f'{REPO_PATH}/pullrequests'),
                ('GET', f'{REPO_PATH}/pullrequests'),
                ('GET', f'{PR_PATH}/threads'),
                ('POST', f'{PR_PATH}/threads/5/comments'),
                ('PATCH', f'{PR_PATH}/threads/5'),
            ],
        )


class MergeDetectionFlow(unittest.TestCase):

    def test_a_completed_pull_request_reads_as_merged_into_its_target(self) -> None:
        merged = pull_request(
            42, 'UNA-1 Fix login', status='completed', targetRefName='refs/heads/develop',
        )
        abandoned = pull_request(41, 'UNA-1 First try', status='abandoned')
        azure = client({
            ('GET', f'{REPO_PATH}/pullrequests'): FakeResponse({'value': [merged, abandoned]}),
        })

        found = azure.find_pull_requests(OWNER, SLUG, source_branch='UNA-1', state='all')

        self.assertEqual(
            [(item['id'], item['state'], item['destination_branch']) for item in found],
            [('42', 'merged', 'develop'), ('41', 'declined', 'main')],
        )
        params = azure.session.calls[0]['kwargs']['params']
        self.assertEqual(params['searchCriteria.status'], 'all')
        self.assertEqual(params['searchCriteria.sourceRefName'], 'refs/heads/UNA-1')


if __name__ == '__main__':
    unittest.main()

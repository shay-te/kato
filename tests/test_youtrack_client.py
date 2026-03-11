import unittest
from unittest.mock import Mock, patch

import bootstrap  # noqa: F401

from openhands_agent.client.youtrack_client import YouTrackClient
from openhands_agent.data_layers.data.task import Task
from utils import assert_client_headers_and_timeout


class Timeout(Exception):
    pass


class YouTrackClientTests(unittest.TestCase):
    def test_uses_configured_retry_count(self) -> None:
        client = YouTrackClient('https://youtrack.example', 'yt-token', max_retries=5)
        self.assertEqual(client.max_retries, 5)

    def test_get_assigned_tasks_builds_query_and_maps_tasks(self) -> None:
        client = YouTrackClient('https://youtrack.example', 'yt-token')
        issue_response = Mock()
        issue_response.json.return_value = [
            {'idReadable': 'PROJ-1', 'summary': 'Fix bug', 'description': 'Details'}
        ]
        comments_response = Mock()
        comments_response.json.return_value = [
            {'text': 'Please keep the fix minimal.', 'author': {'name': 'Product Manager'}}
        ]
        attachments_response = Mock()
        attachments_response.json.return_value = [
            {
                'name': 'notes.txt',
                'mimeType': 'text/plain',
                'charset': 'utf-8',
                'url': '/api/files/notes.txt',
                'metaData': 'plain text',
            },
            {
                'name': 'bug.png',
                'mimeType': 'image/png',
                'url': '/api/files/bug.png',
                'metaData': '1920x1080',
            },
        ]
        text_attachment_response = Mock()
        text_attachment_response.text = 'Stack trace details'

        with patch.object(
            client,
            '_get',
            side_effect=[
                issue_response,
                comments_response,
                attachments_response,
                text_attachment_response,
            ],
        ) as mock_get:
            tasks = client.get_assigned_tasks(
                project='PROJ',
                assignee='me',
                states=['Todo', 'Open'],
            )

        issue_response.raise_for_status.assert_called_once_with()
        comments_response.raise_for_status.assert_called_once_with()
        attachments_response.raise_for_status.assert_called_once_with()
        text_attachment_response.raise_for_status.assert_called_once_with()
        self.assertEqual(len(tasks), 1)
        self.assertIsInstance(tasks[0], Task)
        self.assertEqual(tasks[0].id, "PROJ-1")
        self.assertEqual(tasks[0].summary, "Fix bug")
        self.assertIn('Details', tasks[0].description)
        self.assertIn('Issue comments:', tasks[0].description)
        self.assertIn('Product Manager: Please keep the fix minimal.', tasks[0].description)
        self.assertIn('Text attachments:', tasks[0].description)
        self.assertIn('Attachment notes.txt:\nStack trace details', tasks[0].description)
        self.assertIn('Screenshot attachments:', tasks[0].description)
        self.assertIn('bug.png (1920x1080) /api/files/bug.png', tasks[0].description)
        self.assertEqual(tasks[0].branch_name, "feature/proj-1")
        assert_client_headers_and_timeout(self, client, 'yt-token', 30)
        self.assertEqual(
            mock_get.call_args_list,
            [
                unittest.mock.call(
                    '/api/issues',
                    params={
                        'query': 'project: PROJ assignee: me State: {Todo}, {Open}',
                        'fields': 'idReadable,summary,description',
                    },
                ),
                unittest.mock.call(
                    '/api/issues/PROJ-1/comments',
                    params={'fields': 'id,text,author(login,name)'},
                ),
                unittest.mock.call(
                    '/api/issues/PROJ-1/attachments',
                    params={'fields': 'id,name,mimeType,charset,metaData,url'},
                ),
                unittest.mock.call('/api/files/notes.txt'),
            ],
        )

    def test_add_pull_request_comment_posts_expected_payload(self) -> None:
        client = YouTrackClient('https://youtrack.example', 'yt-token')
        response = Mock()

        with patch.object(client, '_post', return_value=response) as mock_post:
            client.add_pull_request_comment('PROJ-1', 'https://bitbucket/pr/1')

        response.raise_for_status.assert_called_once_with()
        mock_post.assert_called_once_with(
            '/api/issues/PROJ-1/comments',
            json={'text': 'Pull request created: https://bitbucket/pr/1'},
        )

    def test_get_assigned_tasks_retries_on_transient_timeout(self) -> None:
        client = YouTrackClient('https://youtrack.example', 'yt-token')
        issue_response = Mock(status_code=200)
        issue_response.json.return_value = [
            {'idReadable': 'PROJ-1', 'summary': 'Fix bug', 'description': 'Details'}
        ]
        comments_response = Mock(status_code=200)
        comments_response.json.return_value = []
        attachments_response = Mock(status_code=200)
        attachments_response.json.return_value = []

        with patch.object(
            client,
            '_get',
            side_effect=[
                Timeout('read timeout'),
                issue_response,
                comments_response,
                attachments_response,
            ],
        ) as mock_get:
            tasks = client.get_assigned_tasks('PROJ', 'me', ['Todo'])

        self.assertEqual(len(tasks), 1)
        self.assertEqual(mock_get.call_count, 4)

    def test_get_assigned_tasks_skips_malformed_issue_payloads(self) -> None:
        client = YouTrackClient('https://youtrack.example', 'yt-token')
        issue_response = Mock(status_code=200)
        issue_response.json.return_value = [
            {'summary': 'Missing id'},
            {'idReadable': 'PROJ-2', 'summary': 'Valid', 'description': 'Details'},
        ]
        comments_response = Mock(status_code=200)
        comments_response.json.return_value = []
        attachments_response = Mock(status_code=200)
        attachments_response.json.return_value = []

        with patch.object(
            client,
            '_get',
            side_effect=[issue_response, comments_response, attachments_response],
        ):
            tasks = client.get_assigned_tasks('PROJ', 'me', ['Open'])

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].id, 'PROJ-2')

    def test_get_assigned_tasks_handles_comment_and_attachment_failures(self) -> None:
        client = YouTrackClient('https://youtrack.example', 'yt-token')
        issue_response = Mock(status_code=200)
        issue_response.json.return_value = [
            {'idReadable': 'PROJ-1', 'summary': 'Fix bug', 'description': 'Details'}
        ]

        with patch.object(
            client,
            '_get',
            side_effect=[
                issue_response,
                Timeout('comments down'),
                Timeout('comments down'),
                Timeout('comments down'),
                Timeout('attachments down'),
                Timeout('attachments down'),
                Timeout('attachments down'),
            ],
        ):
            tasks = client.get_assigned_tasks('PROJ', 'me', ['Open'])

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].description, 'Details')

    def test_get_assigned_tasks_truncates_long_text_attachments_and_marks_unavailable(self) -> None:
        client = YouTrackClient('https://youtrack.example', 'yt-token')
        issue_response = Mock(status_code=200)
        issue_response.json.return_value = [
            {'idReadable': 'PROJ-1', 'summary': 'Fix bug', 'description': ''}
        ]
        comments_response = Mock(status_code=200)
        comments_response.json.return_value = [None, {'text': None}]
        attachments_response = Mock(status_code=200)
        attachments_response.json.return_value = [
            {
                'name': 'large.txt',
                'mimeType': 'text/plain',
                'charset': 'utf-8',
                'url': '/api/files/large.txt',
            },
            {
                'name': 'broken.txt',
                'mimeType': 'text/plain',
                'charset': 'utf-8',
                'url': '/api/files/broken.txt',
            },
        ]
        large_text_response = Mock(status_code=200)
        large_text_response.text = 'A' * 6000

        with patch.object(
            client,
            '_get',
            side_effect=[
                issue_response,
                comments_response,
                attachments_response,
                large_text_response,
                Timeout('attachment unavailable'),
                Timeout('attachment unavailable'),
                Timeout('attachment unavailable'),
            ],
        ):
            tasks = client.get_assigned_tasks('PROJ', 'me', ['Open'])

        self.assertEqual(len(tasks), 1)
        self.assertIn('No description provided.', tasks[0].description)
        self.assertIn('Attachment large.txt:\n' + ('A' * 5000), tasks[0].description)
        self.assertIn('Attachment broken.txt could not be downloaded.', tasks[0].description)
        self.assertNotIn('A' * 5001, tasks[0].description)

    def test_get_assigned_tasks_rejects_empty_states(self) -> None:
        client = YouTrackClient('https://youtrack.example', 'yt-token')

        with self.assertRaisesRegex(ValueError, 'states must not be empty'):
            client.get_assigned_tasks('PROJ', 'me', [])

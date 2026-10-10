"""An Azure Repos repository, end to end through kato's own inventory.

A real git clone whose origin is an Azure DevOps URL, found by the
``repository_root_path`` walk — the way an operator's repositories reach kato
— then every step kato takes on it: the provider, owner and API base read off
the remote, the token and bot identity from the ``AZURE_*`` settings, the
pull-request client built by the factory, the request that reaches Azure, and
a reviewer's ``@<GUID>`` mention of the bot recognised as addressed to kato.

Only ``requests.Session`` is faked.
"""
from __future__ import annotations

import base64
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from provider_client_base.provider_client_base.helpers.mention_utils import (
    extract_all_mention_tokens,
    mentions_include_identity,
)

from kato_core_lib.data_layers.service.repository_service import RepositoryService

BOT_ID = '6A2C8F1E-0F3B-4C6A-9E1D-3B7C2A9F0D11'


class _Response(object):
    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = ''
        self.headers: dict = {}

    def json(self):
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f'{self.status_code} Client Error')


class AzureRepositoryEndToEndTests(unittest.TestCase):

    def _service(self, remote_url: str) -> RepositoryService:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / 'src'
        clone = root / 'api'
        clone.mkdir(parents=True)
        subprocess.run(['git', 'init', '-q', str(clone)], check=True)
        subprocess.run(['git', '-C', str(clone), 'remote', 'add', 'origin', remote_url], check=True)
        source = types.SimpleNamespace(
            repository_root_path=str(root), repositories=[], ignored_repository_folders='',
            azure_repos=types.SimpleNamespace(
                base_url='', token='az-pat', username='bot@example.com', api_email='',
            ),
        )
        return RepositoryService(source, 1)

    def _http(self, routes: dict) -> list[dict]:
        """Fake every ``requests.Session`` verb; returns the recorded calls."""
        calls: list[dict] = []

        def verb(name):
            def send(session, url, *args, **kwargs):
                calls.append({'verb': name, 'url': url, **kwargs})
                return routes.get((name, url.split('?')[0]), _Response({'message': 'TF404'}, 404))
            return send

        for name in ('get', 'post', 'patch'):
            patcher = patch.object(requests.Session, name, verb(name.upper()))
            patcher.start()
            self.addCleanup(patcher.stop)
        return calls

    def test_a_services_repository(self) -> None:
        service = self._service('https://acme@dev.azure.com/acme/Web%20App/_git/api')
        repo_api = 'https://dev.azure.com/acme/Web%20App/_apis/git/repositories/api'
        calls = self._http({
            ('GET', f'{repo_api}/pullrequests'): _Response({'value': [{
                'pullRequestId': 42, 'title': 'UNA-1 Fix login',
                'sourceRefName': 'refs/heads/UNA-1',
                'repository': {'webUrl': 'https://dev.azure.com/acme/Web%20App/_git/api'},
            }]}),
            ('GET', f'{repo_api}/pullrequests/42/threads'): _Response({'value': [{
                'id': 3, 'status': 'active',
                'comments': [{
                    'id': 1, 'commentType': 'text',
                    'content': f'@<{BOT_ID}> please handle the empty password',
                    'author': {'id': 'R-1', 'uniqueName': 'rev@example.com'},
                }],
            }]}),
            ('GET', f'{repo_api}/pullrequests/42'): _Response({'createdBy': {}, 'reviewers': []}),
            ('GET', 'https://dev.azure.com/acme/_apis/connectionData'): _Response({
                'authenticatedUser': {
                    'id': BOT_ID,
                    'properties': {'Account': {'$value': 'bot@example.com'}},
                },
            }),
        })
        repository = service.get_repository('api')

        found = service.find_pull_requests(repository, source_branch='UNA-1', title_prefix='UNA-1 ')
        self.assertEqual(found, [{
            'id': '42', 'title': 'UNA-1 Fix login',
            'url': 'https://dev.azure.com/acme/Web%20App/_git/api/pullrequest/42',
        }])
        # The PAT, as Azure takes one, on the API host derived from the remote.
        self.assertEqual(
            calls[0]['headers']['Authorization'],
            'Basic ' + base64.b64encode(b':az-pat').decode('ascii'),
        )

        comments = service.list_pull_request_comments(repository, '42')
        self.assertEqual([c.comment_id for c in comments], ['3-1'])
        # The reviewer tagged the bot: kato's mention filter must see it.
        bot = service.review_comment_bot_login('api')
        self.assertEqual(bot, 'bot@example.com')
        self.assertTrue(mentions_include_identity(extract_all_mention_tokens(comments[0].body), (bot,)))

        self.assertEqual(
            service._review_url(repository, 'UNA-1', 'main'),
            'https://dev.azure.com/acme/Web%20App/_git/api/pullrequestcreate'
            '?sourceRef=UNA-1&targetRef=main',
        )

    def test_an_on_prem_azure_devops_server(self) -> None:
        service = self._service('https://tfs.corp/tfs/Coll/proj/_git/api')
        repo_api = 'https://tfs.corp/tfs/Coll/proj/_apis/git/repositories/api'
        calls = self._http({('GET', f'{repo_api}/pullrequests'): _Response({'value': []})})
        repository = service.get_repository('api')

        self.assertEqual(service.find_pull_requests(repository, source_branch='UNA-1'), [])
        # Its own host — no "dev.azure.com" default can be guessed for it.
        self.assertEqual(calls[0]['url'], f'{repo_api}/pullrequests')
        self.assertEqual(repository.provider, 'azure')


if __name__ == '__main__':
    unittest.main()

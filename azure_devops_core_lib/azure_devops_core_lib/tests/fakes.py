"""A fake HTTP layer for the Azure DevOps client: responses and a routed session.

Only the ``requests.Session`` verbs are replaced, so the client's own path
building, retry wrapper and header/timeout merge all run for real and the
recorded calls are exactly what would go on the wire.
"""

from __future__ import annotations

from requests import HTTPError

from azure_devops_core_lib.azure_devops_core_lib.client.azure_devops_client import (
    AzureDevOpsClient,
)

BASE_URL = 'https://dev.azure.com'
TOKEN = 'example-pat'
OWNER = 'acme/Web App'
SLUG = 'api'
# The quoted repository path every call hangs off.
REPO_PATH = '/acme/Web%20App/_apis/git/repositories/api'

BOT_ID = '6A2C8F1E-0F3B-4C6A-9E1D-3B7C2A9F0D11'
ALICE_ID = '11111111-2222-3333-4444-555555555555'
REVIEWER_ID = 'AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE'
STRANGER_ID = '99999999-8888-7777-6666-555555555555'


class FakeResponse(object):
    """Enough of ``requests.Response`` for the client."""

    def __init__(self, json_data=None, *, status_code: int = 200, text: str = '') -> None:
        self._json = json_data
        self.status_code = status_code
        self.text = text
        self.headers: dict = {}

    def json(self):
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise HTTPError(f'{self.status_code} Client Error')


class RoutedSession(object):
    """Answers ``(verb, path)`` from ``routes``; records every call.

    A route's value is a ``FakeResponse`` or a callable taking the call's
    kwargs. Anything unrouted is a 404 carrying an Azure-style message, so a
    test that forgets a route fails loudly instead of passing on ``None``.
    """

    def __init__(self, routes: dict | None = None) -> None:
        self.routes = dict(routes or {})
        self.calls: list[dict] = []

    def _answer(self, verb: str, url: str, kwargs: dict):
        path = url[len(BASE_URL):] if url.startswith(BASE_URL) else url
        self.calls.append({'verb': verb, 'path': path, 'url': url, 'kwargs': kwargs})
        route = self.routes.get((verb, path))
        if route is None:
            return FakeResponse({'message': f'TF404: no route for {verb} {path}'}, status_code=404)
        return route(kwargs) if callable(route) else route

    def get(self, url, *args, **kwargs):
        return self._answer('GET', url, kwargs)

    def post(self, url, *args, **kwargs):
        return self._answer('POST', url, kwargs)

    def patch(self, url, *args, **kwargs):
        return self._answer('PATCH', url, kwargs)

    def paths(self, verb: str = '') -> list[str]:
        return [call['path'] for call in self.calls if not verb or call['verb'] == verb]


def client(routes: dict | None = None) -> AzureDevOpsClient:
    azure = AzureDevOpsClient(BASE_URL, TOKEN, max_retries=1)
    azure.session = RoutedSession(routes)
    return azure


def identity(guid: str, unique_name: str, display_name: str = '') -> dict:
    return {'id': guid, 'uniqueName': unique_name, 'displayName': display_name or unique_name}


def pull_request(pull_request_id: int = 42, title: str = 'UNA-1 Fix login', **extra) -> dict:
    return {
        'pullRequestId': pull_request_id,
        'title': title,
        'sourceRefName': 'refs/heads/UNA-1',
        'targetRefName': 'refs/heads/main',
        'url': f'{BASE_URL}/acme/_apis/git/repositories/r-guid/pullRequests/{pull_request_id}',
        'repository': {'webUrl': f'{BASE_URL}/acme/Web%20App/_git/api'},
        **extra,
    }


def thread(thread_id: int, *comments: dict, status: str | None = 'active', **context) -> dict:
    payload = {'id': thread_id, 'comments': list(comments), 'isDeleted': False}
    if status is not None:
        payload['status'] = status
    if context:
        payload['threadContext'] = context
    return payload


def comment(comment_id: int, content: str, author: dict | None = None, **extra) -> dict:
    return {
        'id': comment_id,
        'content': content,
        'commentType': 'text',
        'author': author or identity(ALICE_ID, 'alice@example.com', 'Alice'),
        **extra,
    }

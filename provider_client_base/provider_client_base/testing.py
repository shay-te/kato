"""Agnostic test helpers shared by every provider/transport lib's test suite.

These were previously in the kato top-level ``tests/utils.py`` (which imports
``kato_core_lib``), so importing them dragged the whole orchestrator onto the
path and stopped each lib's suite from running standalone. They live here in
``provider_client_base`` — the shared base every provider client already
depends on — and use only stdlib + ``provider_client_base`` types, so a lib's
tests stay self-contained and product-agnostic.
"""
from __future__ import annotations

import unittest
from base64 import b64encode
from unittest.mock import Mock

from provider_client_base.provider_client_base.data.fields import (
    ReviewCommentFields,
)
from provider_client_base.provider_client_base.data.issue_record import (
    IssueRecord,
)
from provider_client_base.provider_client_base.data.review_comment import (
    ReviewComment,
)


class ClientTimeout(TimeoutError):
    """Stand-in for a transport timeout in tests (any ``TimeoutError``)."""


def assert_client_headers_and_timeout(
    test_case: unittest.TestCase,
    client: object,
    token: str,
    timeout: int,
) -> None:
    test_case.assertEqual(client.headers, {'Authorization': f'Bearer {token}'})
    test_case.assertEqual(client.timeout, timeout)


def assert_client_basic_auth_and_timeout(
    test_case: unittest.TestCase,
    client: object,
    username: str,
    token: str,
    timeout: int,
) -> None:
    encoded = b64encode(f'{username}:{token}'.encode('utf-8')).decode('ascii')
    test_case.assertEqual(client.headers, {'Authorization': f'Basic {encoded}'})
    test_case.assertEqual(client.timeout, timeout)


def mock_response(
    *,
    json_data=None,
    status_code: int = 200,
    text='',
    content=b'',
) -> Mock:
    response = Mock(status_code=status_code)
    response.json.return_value = json_data
    response.text = text
    response.content = content
    return response


def build_task(
    task_id: str = 'PROJ-1',
    summary: str = 'fix it already',
    description: str = 'Details',
    branch_name: str = 'feature/proj-1',
    tags: list[str] | None = None,
    comments: list[dict] | None = None,
) -> IssueRecord:
    """A neutral issue record (the agnostic ``Task`` interface).

    Returns ``IssueRecord`` — the same duck-typed shape (``id``/``summary``/
    ``description``/``branch_name``/``tags``/``all_comments``) the transports
    read — so tests don't need the kato ``Task`` type.
    """
    record = IssueRecord(
        id=task_id,
        summary=summary,
        description=description,
        branch_name=branch_name,
        tags=list(tags or []),
    )
    if comments is not None:
        record.all_comments = comments
    return record


def build_review_comment(
    pull_request_id: str = '17',
    comment_id: str = '99',
    author: str = 'reviewer',
    body: str = 'Please rename this variable.',
    resolution_target_id: str = '',
    resolution_target_type: str = '',
    resolvable: bool | None = None,
) -> ReviewComment:
    comment = ReviewComment(
        pull_request_id=pull_request_id,
        comment_id=comment_id,
        author=author,
        body=body,
    )
    if resolution_target_id:
        setattr(comment, ReviewCommentFields.RESOLUTION_TARGET_ID, resolution_target_id)
    if resolution_target_type:
        setattr(comment, ReviewCommentFields.RESOLUTION_TARGET_TYPE, resolution_target_type)
    if resolvable is not None:
        setattr(comment, ReviewCommentFields.RESOLVABLE, resolvable)
    return comment


def create_pull_request_with_defaults(
    client,
    title: str = 'PROJ-1: fix it already',
    source_branch: str = 'feature/proj-1',
    repo_owner: str = 'workspace',
    repo_slug: str = 'repo',
    destination_branch: str = 'main',
    description: str = '',
):
    return client.create_pull_request(
        title=title,
        source_branch=source_branch,
        repo_owner=repo_owner,
        repo_slug=repo_slug,
        destination_branch=destination_branch,
        description=description,
    )

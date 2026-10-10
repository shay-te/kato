"""Azure Repos pull requests over the Azure DevOps REST API (7.1).

A repository is named by THREE parts on Azure — organization (a collection on
an on-prem Azure DevOps Server), project, repository — where the other
providers have two. The caller passes the first two as ``repo_owner``
(``"<org>/<project>"``, or ``"tfs/<collection>/<project>"`` on a server under
a virtual directory) and the repository as ``repo_slug``; every request is
then ``{base_url}/{owner}/_apis/git/repositories/{slug}/...``.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from provider_client_base.provider_client_base.data.fields import (
    PullRequestFields,
    ReviewCommentFields,
)
from provider_client_base.provider_client_base.data.review_comment import ReviewComment
from provider_client_base.provider_client_base.pull_request_client_base import (
    PullRequestClientBase,
)
from utils_core_lib.utils_core_lib.text_utils import (
    dict_from_mapping,
    list_from_mapping,
    normalized_text,
    text_from_attr,
    text_from_mapping,
)

from azure_devops_core_lib.azure_devops_core_lib.client.auth import pat_basic_auth_header
from azure_devops_core_lib.azure_devops_core_lib.helpers.identity_mentions import (
    identity_names,
    mentioned_identity_ids,
    rewrite_identity_mentions,
)

API_VERSION = '7.1'
_API = {'api-version': API_VERSION}

# Azure DevOps refuses a pull request whose description is longer than this.
MAX_DESCRIPTION_CHARS = 4000
_TRUNCATED_MARKER = '\n\n…(description truncated)'

# A thread in any of these is resolved. ``active`` / ``pending`` are open, and
# so is ``unknown`` (a thread created without a status): only a thread someone
# closed is left out, the way other providers leave out resolved discussions.
_RESOLVED_THREAD_STATUSES = frozenset({'fixed', 'wontfix', 'closed', 'bydesign'})

# Azure numbers comments per THREAD (every thread starts at 1), so a comment
# id alone is not unique on a pull request. The id handed out joins the two.
_COMMENT_ID_SEPARATOR = '-'


class AzureDevOpsClient(PullRequestClientBase):
    provider_name = 'azure'

    def __init__(self, base_url: str, token: str, max_retries: int = 3) -> None:
        super().__init__(base_url, token, timeout=30, max_retries=max_retries)
        self.set_headers({'Authorization': pat_basic_auth_header(token)})
        # ``{organization path: {guid: name}}`` — the PAT's own identity, read
        # once per organization (it is the account replying as the bot).
        self._authenticated_names: dict[str, dict[str, str]] = {}

    # ----- the provider interface -----

    def validate_connection(self, repo_owner: str, repo_slug: str) -> None:
        response = self._get_with_retry(self._repository_path(repo_owner, repo_slug), params=_API)
        self.raise_for_status_with_detail(response)

    def create_pull_request(
        self,
        title: str,
        source_branch: str,
        repo_owner: str,
        repo_slug: str,
        destination_branch: str | None = None,
        description: str = '',
    ) -> dict[str, str]:
        target = normalized_text(destination_branch) or self._default_branch(repo_owner, repo_slug)
        response = self._post_with_retry(
            f'{self._repository_path(repo_owner, repo_slug)}/pullrequests',
            params=_API,
            json={
                'sourceRefName': _branch_ref(source_branch),
                'targetRefName': _branch_ref(target),
                PullRequestFields.TITLE: title,
                PullRequestFields.DESCRIPTION: _fitted_description(description),
            },
        )
        # Detail-preserving: Azure's refusal is a TF code and one sentence
        # ("TF401179: An active pull request for the source and target branch
        # already exists") that a bare status line would throw away.
        self.raise_for_status_with_detail(response)
        return self._normalize_pr(response.json())

    def find_pull_requests(
        self,
        repo_owner: str,
        repo_slug: str,
        *,
        source_branch: str = '',
        title_prefix: str = '',
    ) -> list[dict[str, str]]:
        params = {**_API, 'searchCriteria.status': 'active', '$top': 100}
        normalized_source_branch = normalized_text(source_branch)
        if normalized_source_branch:
            params['searchCriteria.sourceRefName'] = _branch_ref(normalized_source_branch)
        response = self._get_with_retry(
            f'{self._repository_path(repo_owner, repo_slug)}/pullrequests', params=params,
        )
        response.raise_for_status()
        normalized_title_prefix = normalized_text(title_prefix)
        matches: list[dict[str, str]] = []
        for item in _values(response.json()):
            if normalized_source_branch and normalized_text(
                item.get('sourceRefName', ''),
            ) != _branch_ref(normalized_source_branch):
                continue
            item_title = normalized_text(item.get(PullRequestFields.TITLE, ''))
            if normalized_title_prefix and not item_title.startswith(normalized_title_prefix):
                continue
            matches.append(self._normalize_pr(item))
        return matches

    def list_pull_request_comments(
        self,
        repo_owner: str,
        repo_slug: str,
        pull_request_id: str,
    ) -> list[ReviewComment]:
        pull_request_path = self._pull_request_path(repo_owner, repo_slug, pull_request_id)
        response = self._get_with_retry(f'{pull_request_path}/threads', params=_API)
        response.raise_for_status()
        threads = [
            thread for thread in _values(response.json())
            if not thread.get('isDeleted')
            and normalized_text(thread.get('status', '')).lower() not in _RESOLVED_THREAD_STATUSES
        ]
        names = self._mention_names(repo_owner, repo_slug, pull_request_id, threads)
        comments: list[ReviewComment] = []
        for thread in threads:
            comments.extend(self._thread_comments(thread, pull_request_id, names))
        return comments

    def reply_to_review_comment(
        self,
        repo_owner: str,
        repo_slug: str,
        comment: ReviewComment,
        body: str,
    ) -> None:
        thread_id = _require_thread_id(comment)
        response = self._post_with_retry(
            f'{self._pull_request_path(repo_owner, repo_slug, comment.pull_request_id)}'
            f'/threads/{thread_id}/comments',
            params=_API,
            json={
                'content': normalized_text(body),
                'parentCommentId': _thread_comment_number(comment.comment_id),
                'commentType': 1,  # text
            },
        )
        response.raise_for_status()

    def resolve_review_comment(
        self,
        repo_owner: str,
        repo_slug: str,
        comment: ReviewComment,
    ) -> None:
        thread_id = _require_thread_id(comment)
        response = self._patch_with_retry(
            f'{self._pull_request_path(repo_owner, repo_slug, comment.pull_request_id)}'
            f'/threads/{thread_id}',
            params=_API,
            json={'status': 'fixed'},
        )
        response.raise_for_status()

    # ----- paths -----

    @staticmethod
    def _quoted_path(path: str) -> str:
        """Each segment quoted (a project can be ``My Project``), ``/`` kept."""
        return '/'.join(quote(part, safe='') for part in str(path or '').split('/') if part)

    def _repository_path(self, repo_owner: str, repo_slug: str) -> str:
        return (
            f'/{self._quoted_path(repo_owner)}/_apis/git/repositories/'
            f'{quote(normalized_text(repo_slug), safe="")}'
        )

    def _pull_request_path(self, repo_owner: str, repo_slug: str, pull_request_id: object) -> str:
        return (
            f'{self._repository_path(repo_owner, repo_slug)}/pullrequests/'
            f'{quote(normalized_text(pull_request_id), safe="")}'
        )

    # ----- pull requests -----

    def _default_branch(self, repo_owner: str, repo_slug: str) -> str:
        response = self._get_with_retry(self._repository_path(repo_owner, repo_slug), params=_API)
        self.raise_for_status_with_detail(response)
        branch = text_from_mapping(response.json(), 'defaultBranch')
        if not branch:
            # A repository with no commits has no default branch, and there
            # is nothing a pull request could be opened into.
            raise ValueError(
                f'repository {repo_owner}/{repo_slug} has no default branch; '
                'pass the destination branch explicitly'
            )
        return branch

    @classmethod
    def _normalize_pr(cls, payload: Any) -> dict[str, str]:
        """``{id, title, url}`` with the WEB url — the payload's own ``url``
        is the REST resource, which is no use to a person."""
        web_url = normalized_text(
            dict_from_mapping(payload, 'repository').get('webUrl', ''),
        ).rstrip('/')
        pull_request_id = text_from_mapping(payload, 'pullRequestId')
        return cls._normalized_pull_request(
            payload,
            id_key='pullRequestId',
            url=f'{web_url}/pullrequest/{pull_request_id}' if web_url else '',
        )

    # ----- review comments -----

    @classmethod
    def _thread_comments(
        cls, thread: dict[str, Any], pull_request_id: str, names: dict[str, str],
    ) -> list[ReviewComment]:
        thread_id = text_from_mapping(thread, 'id')
        if not thread_id:
            return []
        context = dict_from_mapping(thread, 'threadContext')
        file_path = normalized_text(context.get('filePath', '')).lstrip('/')
        # Right = the pull request's side (new code), left = the base (removed).
        right_line = dict_from_mapping(context, 'rightFileStart').get('line')
        left_line = dict_from_mapping(context, 'leftFileStart').get('line')
        if right_line:
            line_number, line_type = right_line, 'added'
        elif left_line:
            line_number, line_type = left_line, 'removed'
        else:
            line_number, line_type = '', ''
        comments: list[ReviewComment] = []
        for item in list_from_mapping(thread, 'comments'):
            if not isinstance(item, dict) or item.get('isDeleted'):
                continue
            # Votes, pushes and status changes arrive as ``system`` comments.
            if normalized_text(item.get('commentType', '')).lower() == 'system':
                continue
            comment_number = text_from_mapping(item, 'id')
            if not comment_number:
                continue
            author = dict_from_mapping(item, 'author')
            comments.append(cls._review_comment_from_values(
                pull_request_id=pull_request_id,
                comment_id=f'{thread_id}{_COMMENT_ID_SEPARATOR}{comment_number}',
                author=author.get('uniqueName') or author.get('displayName') or '',
                author_id=author.get('id', ''),
                body=rewrite_identity_mentions(item.get('content', ''), names),
                resolution_target_id=thread_id,
                resolution_target_type='thread',
                resolvable=True,
                file_path=file_path,
                line_number=line_number,
                line_type=line_type,
            ))
        return comments

    def _mention_names(
        self, repo_owner: str, repo_slug: str, pull_request_id: str, threads: list[dict],
    ) -> dict[str, str]:
        """``{guid: name}`` for every identity the threads @-mention.

        The commenters come with the threads; the pull request's author and
        reviewers, and the PAT's own account, cost a request each — made only
        when a mention is not already resolved. Best-effort throughout: a
        lookup that fails leaves that mention as its GUID, and never costs
        the listing.
        """
        comments = [
            item for thread in threads for item in list_from_mapping(thread, 'comments')
            if isinstance(item, dict)
        ]
        names = identity_names(dict_from_mapping(item, 'author') for item in comments)
        wanted = set().union(*(mentioned_identity_ids(item.get('content')) for item in comments))
        if wanted - names.keys():
            for guid, name in self._pull_request_identities(
                repo_owner, repo_slug, pull_request_id,
            ).items():
                names.setdefault(guid, name)
        if wanted - names.keys():
            for guid, name in self._authenticated_identity(repo_owner).items():
                names.setdefault(guid, name)
        return names

    def _pull_request_identities(
        self, repo_owner: str, repo_slug: str, pull_request_id: str,
    ) -> dict[str, str]:
        try:
            response = self._get_with_retry(
                self._pull_request_path(repo_owner, repo_slug, pull_request_id), params=_API,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            self.logger.warning(
                'could not read pull request %s to resolve its @-mentions', pull_request_id,
            )
            return {}
        return identity_names([
            dict_from_mapping(payload, 'createdBy'),
            *list_from_mapping(payload, 'reviewers'),
        ])

    def _authenticated_identity(self, repo_owner: str) -> dict[str, str]:
        """The PAT's own account, from the organization's ``connectionData``."""
        organization = '/'.join(self._quoted_path(repo_owner).split('/')[:-1])
        if organization in self._authenticated_names:
            return self._authenticated_names[organization]
        names: dict[str, str] = {}
        try:
            response = self._get_with_retry(f'/{organization}/_apis/connectionData')
            response.raise_for_status()
            user = dict_from_mapping(response.json(), 'authenticatedUser')
            account = dict_from_mapping(dict_from_mapping(user, 'properties'), 'Account')
            names = identity_names([{
                'id': user.get('id'),
                'uniqueName': account.get('$value'),
                'displayName': user.get('providerDisplayName'),
            }])
        except Exception:
            self.logger.warning(
                'could not read the authenticated Azure DevOps identity for %s', organization,
            )
        self._authenticated_names[organization] = names
        return names


def _branch_ref(branch: object) -> str:
    """A full ref: Azure names branches ``refs/heads/<name>`` everywhere."""
    text = normalized_text(branch)
    return text if text.startswith('refs/') else f'refs/heads/{text}'


def _fitted_description(description: object) -> str:
    """The description, cut to what Azure accepts rather than refused whole."""
    text = normalized_text(description)
    if len(text) <= MAX_DESCRIPTION_CHARS:
        return text
    return text[:MAX_DESCRIPTION_CHARS - len(_TRUNCATED_MARKER)] + _TRUNCATED_MARKER


def _values(payload: object) -> list[dict[str, Any]]:
    """The ``value`` list of an Azure collection response, dicts only."""
    if not isinstance(payload, dict):
        return []
    return [item for item in list_from_mapping(payload, 'value') if isinstance(item, dict)]


def _require_thread_id(comment: ReviewComment) -> str:
    thread_id = text_from_attr(comment, ReviewCommentFields.RESOLUTION_TARGET_ID)
    if not thread_id:
        # Comment ids are only unique within a thread, so there is no way to
        # find the thread from the comment alone.
        raise ValueError(
            f'unable to determine the Azure DevOps thread for comment {comment.comment_id}'
        )
    return thread_id


def _thread_comment_number(comment_id: object) -> int:
    """The comment's number within its thread (the part after the thread)."""
    number = normalized_text(comment_id).rsplit(_COMMENT_ID_SEPARATOR, 1)[-1]
    try:
        return int(number)
    except ValueError:
        return 0

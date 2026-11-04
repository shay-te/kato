"""How to approve a repository for agent work, said the same way everywhere.

Approvals live in kato's Settings → Approvals tab. The command-line picker that
used to do it is gone, and messages that still named it — or a Settings tab
that holds no approvals — sent a new user to a dead end on their first task.
Every message about an unapproved repository builds its words here.
"""

from __future__ import annotations

from collections.abc import Iterable

APPROVALS_LOCATION = 'Settings → Approvals'
SANDBOX_LOCATION = 'Settings → Sandbox & permission bypass'


def repository_list_text(repository_ids: Iterable[object]) -> str:
    """``api``, ``api and web``, ``api, web and lib`` ('' for none)."""
    ids = [str(repository_id).strip() for repository_id in repository_ids if str(repository_id).strip()]
    if len(ids) <= 1:
        return ''.join(ids)
    return f'{", ".join(ids[:-1])} and {ids[-1]}'


def approve_repositories_hint(repository_ids: Iterable[object]) -> str:
    """The one sentence that says how to approve these repositories."""
    names = repository_list_text(repository_ids) or 'the repository'
    return f'Approve {names} in {APPROVALS_LOCATION}, then try again.'


def not_approved_text(repository_ids: Iterable[object]) -> str:
    """Why kato refused, and how to fix it."""
    ids = list(repository_ids)
    names = repository_list_text(ids) or 'a repository'
    verb = 'is' if len(ids) <= 1 else 'are'
    return f'{names} {verb} not approved for agent work yet. {approve_repositories_hint(ids)}'


# What restricted mode needs that the running kato does not have, each with
# where to change it. The scanner threshold has no setting yet.
RESTRICTED_NEEDS_BYPASS_OFF = f'the permission bypass turned off ({SANDBOX_LOCATION})'
RESTRICTED_NEEDS_DOCKER = f'the Docker sandbox turned on ({SANDBOX_LOCATION})'
RESTRICTED_NEEDS_SCANNER_MEDIUM = (
    'the security scanner blocking MEDIUM findings (security_scanner.block_on_severity in '
    "kato's config file — not in Settings yet)"
)


def restricted_mode_refusal_text(repository_ids: Iterable[object], needs: Iterable[str]) -> str:
    """A restricted-mode repository on a kato that does not meet it."""
    ids = list(repository_ids)
    names = repository_list_text(ids) or 'a repository'
    one = len(ids) <= 1
    missing = '; '.join(needs) or 'a stricter setup'
    return (
        f'{names} {"is" if one else "are"} approved in restricted mode, which also needs {missing}. '
        f'Switch {"it" if one else "them"} to trusted in {APPROVALS_LOCATION}, '
        f'or set what is missing and restart kato.'
    )

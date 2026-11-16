"""Why a git host refused kato — and what to change — in ONE place.

A bare ``403 Client Error: Forbidden``, once per repository on every scan,
tells the operator nothing they can act on. Each host refuses for its own
reasons, and the fix is a setting or a token permission they cannot guess:

* Bitbucket: an API token carries SCOPES, and reading a repository is a
  different scope from reading its pull requests. A token made with repository
  scopes only passed every connection check (they read the repository) and
  then got 403 on every pull request lookup (2026-11-15: "what token
  permissions is missing?"). Scopes cannot be added to a token afterwards.
* GitHub hides a missing WRITE permission behind 404, not 403.
* An Atlassian API token authenticates as EMAIL + token — Bitbucket says so in
  its own body ("must be used with an atlassian registered email").

Read from the HOST'S OWN ERROR TEXT, never from settings: the host is the
authority on why it refused. Every place that shows a git host's refusal uses
``refusal_hint``: the review scan's log line and the "Kato can't reach" banner
(review_comment_service), Settings' Test connection (git_host_connection_check)
and the pull request step (task_publish_service). Words point at Settings,
never at an env var.
"""

from __future__ import annotations

from kato_core_lib.data_layers.service.connection_health import provider_label

# What a Bitbucket API token ("Create API token with scopes" → Bitbucket) needs
# for everything kato does: push (repository write), find, open, reply on and
# decline pull requests (pull request read + write).
BITBUCKET_TOKEN_SCOPES = (
    'read:repository:bitbucket', 'write:repository:bitbucket',
    'read:pullrequest:bitbucket', 'write:pullrequest:bitbucket',
)
GITHUB_TOKEN_GRANTS = (
    'a classic token needs the `repo` scope; a fine-grained token needs '
    'this repository selected plus Contents: Read and write, Pull '
    'requests: Read and write, and Metadata: Read'
)


def git_host_settings_place(provider: str) -> str:
    """Where an operator sets a git host's connection — never an env var name."""
    return f'Settings → Git provider → {provider_label(provider)}'


def refusal_hint(error: object) -> str:
    """`` — <what to change>`` for a git host's refusal, or ``''`` when the
    error is not a refusal (a rate limit, a network blip: pointing at the
    credentials for those sends the operator the wrong way)."""
    text = str(error or '')
    lowered = text.lower()
    if 'atlassian registered email' in lowered:
        return (
            ' — an Atlassian API token signs in with EMAIL + token: set API email to the '
            f'Atlassian account email that owns it, in {git_host_settings_place("bitbucket")}'
        )
    if 'bitbucket' in lowered:
        return _bitbucket(lowered)
    if 'github.com' in lowered:
        return _github(lowered)
    if 'gitlab' in lowered:
        return _gitlab(lowered)
    if 'dev.azure.com' in lowered or '.visualstudio.com' in lowered or 'tf400813' in lowered:
        return (
            ' — the Azure DevOps token needs the Code (Read & Write) scope for this organization '
            f'and must not have expired: check it in {git_host_settings_place("azure")}'
        )
    if '401' in text and 'unauthorized' in lowered:
        return ' — the git host rejected the token: check it in Settings → Git provider'
    return ''


def _bitbucket(lowered: str) -> str:
    place = git_host_settings_place('bitbucket')
    if '403' in lowered:
        return (
            ' — Bitbucket accepted the token but it lacks a permission. A Bitbucket API token '
            f'needs the scopes {", ".join(BITBUCKET_TOKEN_SCOPES)} (repository scopes alone '
            'cannot read pull requests). Scopes cannot be added to a token: create a new one with '
            f'them and paste it in {place}'
        )
    if '401' in lowered:
        return (
            ' — Bitbucket rejected the token: check it, and that API email is the Atlassian '
            f'account email that owns it, in {place}'
        )
    return ''


def _github(lowered: str) -> str:
    place = git_host_settings_place('github')
    if '404' in lowered:
        return (
            ' — GitHub answers 404 (not 403) when a token cannot WRITE to a repository, so '
            f'this is almost always a permissions gap: {GITHUB_TOKEN_GRANTS}. Token: {place}'
        )
    if '403' in lowered:
        return f' — the GitHub token is missing a permission: {GITHUB_TOKEN_GRANTS}. Token: {place}'
    if '401' in lowered:
        return f' — GitHub rejected the token outright: check it is set and has not expired, in {place}'
    return ''


def _gitlab(lowered: str) -> str:
    place = git_host_settings_place('gitlab')
    if '403' in lowered:
        return (
            ' — the GitLab token lacks a permission: it needs the api scope (a project or group '
            f'token: the Developer role or higher). Token: {place}'
        )
    if '401' in lowered:
        return f' — GitLab rejected the token: check it is set and has not expired, in {place}'
    return ''

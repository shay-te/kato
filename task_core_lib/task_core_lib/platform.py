from __future__ import annotations

from enum import Enum


class Platform(Enum):
    """Task platforms supported by task_core_lib."""

    YOUTRACK = 'youtrack'
    JIRA = 'jira'
    GITHUB = 'github'
    GITHUB_ISSUES = 'github_issues'
    GITLAB = 'gitlab'
    GITLAB_ISSUES = 'gitlab_issues'
    BITBUCKET = 'bitbucket'
    BITBUCKET_ISSUES = 'bitbucket_issues'

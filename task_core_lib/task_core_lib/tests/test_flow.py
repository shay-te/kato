"""A-Z flow tests for task_core_lib.

Each test walks the full stack — Platform selection, TaskCoreLib
construction, TaskClientFactory routing, and the resulting issue
provider — using injected provider factories so no platform libraries
are required.

Flow coverage
-------------
F1   YouTrack: end-to-end from Platform + config to .issue
F2   Jira: end-to-end
F3   GitHub: end-to-end
F4   GitHub Issues alias: routes to the same provider as GitHub
F5   GitLab: end-to-end
F6   GitLab Issues alias: routes to the same provider as GitLab
F7   Bitbucket: end-to-end
F8   Bitbucket Issues alias: routes to the same provider as Bitbucket
F9   Unknown platform: .issue is None, no exception raised
F10  max_retries propagated from TaskCoreLib down to the factory
F11  Config object propagated intact from TaskCoreLib to factory
F12  Multiple platforms registered; each TaskCoreLib instance gets own provider
F13  Default path (no provider_factories): all five platform branches reachable
     via lazy-import patching
F14  Platform enum lookups by value match the routing keys used in the factory
"""
from __future__ import annotations

import sys
import unittest
from unittest.mock import MagicMock, patch

from omegaconf import OmegaConf

from task_core_lib.task_core_lib.client.task_client_factory import TaskClientFactory
from task_core_lib.task_core_lib.platform import Platform
from task_core_lib.task_core_lib.task_core_lib import TaskCoreLib


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _cfg(**kwargs):
    base = {'base_url': 'https://example.com', 'token': 'secret'}
    base.update(kwargs)
    return OmegaConf.create(base)


def _mock_sys_lib(dotted_path: str):
    """Context manager that injects MagicMocks for *dotted_path* and its
    parent segments into ``sys.modules``."""
    parts = dotted_path.split('.')
    mocks = {}
    for i in range(len(parts)):
        mocks['.'.join(parts[: i + 1])] = MagicMock()
    leaf = MagicMock()
    mocks[dotted_path] = leaf
    return patch.dict(sys.modules, mocks), leaf


def _simple_provider(platform):
    """Return ``(factories_dict, issue_mock)`` for *platform*."""
    issue = MagicMock(name=f'{platform.value}_issue')
    return {platform: lambda cfg, max_retries: issue}, issue


# ---------------------------------------------------------------------------
# F1–F8: per-platform end-to-end flows via injection
# ---------------------------------------------------------------------------


class F1YouTrackFlowTest(unittest.TestCase):
    def test_youtrack_issue_provider_returned(self) -> None:
        factories, issue = _simple_provider(Platform.YOUTRACK)
        lib = TaskCoreLib(Platform.YOUTRACK, _cfg(), 3, provider_factories=factories)
        self.assertIs(lib.issue, issue)


class F2JiraFlowTest(unittest.TestCase):
    def test_jira_issue_provider_returned(self) -> None:
        factories, issue = _simple_provider(Platform.JIRA)
        lib = TaskCoreLib(Platform.JIRA, _cfg(), 3, provider_factories=factories)
        self.assertIs(lib.issue, issue)


class F3GitHubFlowTest(unittest.TestCase):
    def test_github_issue_provider_returned(self) -> None:
        factories, issue = _simple_provider(Platform.GITHUB)
        lib = TaskCoreLib(Platform.GITHUB, _cfg(), 3, provider_factories=factories)
        self.assertIs(lib.issue, issue)


class F4GitHubIssuesAliasFlowTest(unittest.TestCase):
    def test_github_issues_alias_routed_correctly(self) -> None:
        factories, issue = _simple_provider(Platform.GITHUB_ISSUES)
        lib = TaskCoreLib(Platform.GITHUB_ISSUES, _cfg(), 3, provider_factories=factories)
        self.assertIs(lib.issue, issue)

    def test_github_and_github_issues_are_independent_keys(self) -> None:
        gh_issue = MagicMock(name='gh')
        ghi_issue = MagicMock(name='ghi')
        factories = {
            Platform.GITHUB: lambda cfg, r: gh_issue,
            Platform.GITHUB_ISSUES: lambda cfg, r: ghi_issue,
        }
        self.assertIs(
            TaskCoreLib(Platform.GITHUB, _cfg(), 1, provider_factories=factories).issue,
            gh_issue,
        )
        self.assertIs(
            TaskCoreLib(Platform.GITHUB_ISSUES, _cfg(), 1, provider_factories=factories).issue,
            ghi_issue,
        )


class F5GitLabFlowTest(unittest.TestCase):
    def test_gitlab_issue_provider_returned(self) -> None:
        factories, issue = _simple_provider(Platform.GITLAB)
        lib = TaskCoreLib(Platform.GITLAB, _cfg(), 3, provider_factories=factories)
        self.assertIs(lib.issue, issue)


class F6GitLabIssuesAliasFlowTest(unittest.TestCase):
    def test_gitlab_issues_alias_routed_correctly(self) -> None:
        factories, issue = _simple_provider(Platform.GITLAB_ISSUES)
        lib = TaskCoreLib(Platform.GITLAB_ISSUES, _cfg(), 3, provider_factories=factories)
        self.assertIs(lib.issue, issue)


class F7BitbucketFlowTest(unittest.TestCase):
    def test_bitbucket_issue_provider_returned(self) -> None:
        factories, issue = _simple_provider(Platform.BITBUCKET)
        lib = TaskCoreLib(Platform.BITBUCKET, _cfg(), 3, provider_factories=factories)
        self.assertIs(lib.issue, issue)


class F8BitbucketIssuesAliasFlowTest(unittest.TestCase):
    def test_bitbucket_issues_alias_routed_correctly(self) -> None:
        factories, issue = _simple_provider(Platform.BITBUCKET_ISSUES)
        lib = TaskCoreLib(Platform.BITBUCKET_ISSUES, _cfg(), 3, provider_factories=factories)
        self.assertIs(lib.issue, issue)


# ---------------------------------------------------------------------------
# F9: Unknown platform → None issue
# ---------------------------------------------------------------------------


class F9UnknownPlatformFlowTest(unittest.TestCase):
    def test_unknown_platform_issue_is_none(self) -> None:
        lib = TaskCoreLib(
            Platform.YOUTRACK, _cfg(), 1,
            provider_factories={},  # no factory registered for YOUTRACK
        )
        self.assertIsNone(lib.issue)

    def test_no_exception_raised_for_unknown_platform(self) -> None:
        try:
            TaskCoreLib(Platform.GITLAB, _cfg(), 1, provider_factories={})
        except Exception as exc:  # noqa: BLE001
            self.fail(f'TaskCoreLib raised unexpectedly: {exc}')


# ---------------------------------------------------------------------------
# F10: max_retries propagation
# ---------------------------------------------------------------------------


class F10MaxRetriesPropagationTest(unittest.TestCase):
    def test_max_retries_reaches_factory(self) -> None:
        received = {}

        def factory(cfg, max_retries):
            received['max_retries'] = max_retries
            return MagicMock()

        TaskCoreLib(
            Platform.JIRA, _cfg(), 17,
            provider_factories={Platform.JIRA: factory},
        )
        self.assertEqual(received['max_retries'], 17)

    def test_different_max_retries_values_all_propagate(self) -> None:
        for retries in (1, 5, 10, 100):
            received = {}

            def factory(cfg, mr, _r=retries):
                received['mr'] = mr
                return MagicMock()

            TaskCoreLib(
                Platform.YOUTRACK, _cfg(), retries,
                provider_factories={Platform.YOUTRACK: factory},
            )
            self.assertEqual(received['mr'], retries,
                             f'max_retries={retries} not propagated')


# ---------------------------------------------------------------------------
# F11: Config object propagation
# ---------------------------------------------------------------------------


class F11ConfigPropagationTest(unittest.TestCase):
    def test_config_object_identity_preserved(self) -> None:
        received = {}

        def factory(cfg, max_retries):
            received['cfg'] = cfg
            return MagicMock()

        cfg = _cfg(token='unique-tok')
        TaskCoreLib(
            Platform.GITLAB, cfg, 1,
            provider_factories={Platform.GITLAB: factory},
        )
        self.assertIs(received['cfg'], cfg)

    def test_config_fields_accessible_in_factory(self) -> None:
        received = {}

        def factory(cfg, max_retries):
            received['token'] = cfg.token
            received['base_url'] = cfg.base_url
            return MagicMock()

        cfg = _cfg(base_url='https://custom.example.com', token='my-secret')
        TaskCoreLib(
            Platform.BITBUCKET, cfg, 1,
            provider_factories={Platform.BITBUCKET: factory},
        )
        self.assertEqual(received['token'], 'my-secret')
        self.assertEqual(received['base_url'], 'https://custom.example.com')


# ---------------------------------------------------------------------------
# F12: Multiple platforms — each instance gets its own provider
# ---------------------------------------------------------------------------


class F12MultiPlatformFlowTest(unittest.TestCase):
    def test_each_platform_gets_own_provider(self) -> None:
        issues = {p: MagicMock(name=p.value) for p in Platform}
        factories = {p: (lambda iss: lambda cfg, r: iss)(issues[p]) for p in Platform}

        for platform in Platform:
            lib = TaskCoreLib(platform, _cfg(), 1, provider_factories=factories)
            self.assertIs(lib.issue, issues[platform],
                          f'{platform.value}: wrong issue provider returned')

    def test_two_instances_same_platform_independent(self) -> None:
        call_count = [0]

        def factory(cfg, max_retries):
            call_count[0] += 1
            return MagicMock()

        factories = {Platform.YOUTRACK: factory}
        TaskCoreLib(Platform.YOUTRACK, _cfg(), 1, provider_factories=factories)
        TaskCoreLib(Platform.YOUTRACK, _cfg(), 1, provider_factories=factories)
        self.assertEqual(call_count[0], 2)


# ---------------------------------------------------------------------------
# F13: Default path — lazy-import patching (no provider_factories)
# ---------------------------------------------------------------------------


class F13DefaultPathFlowTest(unittest.TestCase):
    """Tests that each _build_default branch is reachable and wires up
    the issue correctly, without needing the real platform libs installed."""

    def _run_default(self, platform, lib_dotted, class_name):
        ctx, leaf = _mock_sys_lib(lib_dotted)
        with ctx:
            factory = TaskClientFactory(_cfg(), 3)
            result = factory._build_default(platform)
        cls = getattr(leaf, class_name)
        self.assertIs(result, cls.return_value.issue)
        return cls

    def test_youtrack_default_path(self) -> None:
        self._run_default(
            Platform.YOUTRACK,
            'youtrack_core_lib.youtrack_core_lib.youtrack_core_lib',
            'YouTrackCoreLib',
        )

    def test_jira_default_path(self) -> None:
        self._run_default(
            Platform.JIRA,
            'jira_core_lib.jira_core_lib.jira_core_lib',
            'JiraCoreLib',
        )

    def test_bitbucket_default_path(self) -> None:
        self._run_default(
            Platform.BITBUCKET,
            'bitbucket_core_lib.bitbucket_core_lib.bitbucket_core_lib',
            'BitbucketCoreLib',
        )

    def test_bitbucket_issues_default_path(self) -> None:
        self._run_default(
            Platform.BITBUCKET_ISSUES,
            'bitbucket_core_lib.bitbucket_core_lib.bitbucket_core_lib',
            'BitbucketCoreLib',
        )

    def test_github_default_path(self) -> None:
        self._run_default(
            Platform.GITHUB,
            'github_core_lib.github_core_lib.github_core_lib',
            'GitHubCoreLib',
        )

    def test_github_issues_default_path(self) -> None:
        self._run_default(
            Platform.GITHUB_ISSUES,
            'github_core_lib.github_core_lib.github_core_lib',
            'GitHubCoreLib',
        )

    def test_gitlab_default_path(self) -> None:
        self._run_default(
            Platform.GITLAB,
            'gitlab_core_lib.gitlab_core_lib.gitlab_core_lib',
            'GitLabCoreLib',
        )

    def test_gitlab_issues_default_path(self) -> None:
        self._run_default(
            Platform.GITLAB_ISSUES,
            'gitlab_core_lib.gitlab_core_lib.gitlab_core_lib',
            'GitLabCoreLib',
        )


# ---------------------------------------------------------------------------
# F14: Platform enum lookup by value matches routing keys
# ---------------------------------------------------------------------------


class F14PlatformEnumValueFlowTest(unittest.TestCase):
    """Platform enum members looked up by value must resolve to the same
    routing keys the factory uses — guards against value/name divergence."""

    def test_platform_value_lookup_routes_correctly(self) -> None:
        cases = [
            ('youtrack', Platform.YOUTRACK),
            ('jira', Platform.JIRA),
            ('github', Platform.GITHUB),
            ('github_issues', Platform.GITHUB_ISSUES),
            ('gitlab', Platform.GITLAB),
            ('gitlab_issues', Platform.GITLAB_ISSUES),
            ('bitbucket', Platform.BITBUCKET),
            ('bitbucket_issues', Platform.BITBUCKET_ISSUES),
        ]
        for value, expected_member in cases:
            looked_up = Platform(value)
            self.assertIs(looked_up, expected_member,
                          f"Platform('{value}') did not return {expected_member!r}")

            # Confirm the looked-up member routes to the right factory.
            issue = MagicMock(name=value)
            factories = {expected_member: lambda cfg, r, _i=issue: _i}
            lib = TaskCoreLib(looked_up, _cfg(), 1, provider_factories=factories)
            self.assertIs(lib.issue, issue,
                          f"Platform('{value}') not routed to correct factory")


if __name__ == '__main__':
    unittest.main()

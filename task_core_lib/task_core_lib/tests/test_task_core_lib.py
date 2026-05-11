"""Full coverage for task_core_lib.py — TaskCoreLib class."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from omegaconf import OmegaConf

from task_core_lib.task_core_lib.platform import Platform
from task_core_lib.task_core_lib.task_core_lib import TaskCoreLib


def _make_issue():
    return MagicMock(name='issue_provider')


def _provider(issue=None):
    """Return a factory callable that yields *issue* (or a fresh Mock)."""
    _issue = issue or _make_issue()
    return MagicMock(return_value=_issue), _issue


def _minimal_cfg(**kwargs):
    base = {'base_url': 'https://example.com', 'token': 'tok'}
    base.update(kwargs)
    return OmegaConf.create(base)


# ---------------------------------------------------------------------------
# Structural tests
# ---------------------------------------------------------------------------


class TaskCoreLibStructureTests(unittest.TestCase):
    def test_does_not_inherit_from_external_CoreLib(self) -> None:
        # TaskCoreLib must be a plain class — no dependency on core_lib.CoreLib.
        bases = [b.__name__ for b in TaskCoreLib.__mro__]
        self.assertNotIn('CoreLib', bases)

    def test_issue_attribute_is_set_on_construction(self) -> None:
        fn, issue = _provider()
        lib = TaskCoreLib(
            Platform.YOUTRACK, _minimal_cfg(), 3,
            provider_factories={Platform.YOUTRACK: fn},
        )
        self.assertIs(lib.issue, issue)

    def test_issue_comes_from_factory_not_class_itself(self) -> None:
        fn, issue = _provider()
        lib = TaskCoreLib(
            Platform.JIRA, _minimal_cfg(), 1,
            provider_factories={Platform.JIRA: fn},
        )
        self.assertIs(lib.issue, issue)
        fn.assert_called_once()


# ---------------------------------------------------------------------------
# All 8 platforms via provider_factories injection
# ---------------------------------------------------------------------------


class TaskCoreLibPlatformTests(unittest.TestCase):
    def _assert_issue_set(self, platform: Platform) -> None:
        fn, issue = _provider()
        lib = TaskCoreLib(
            platform, _minimal_cfg(), 2,
            provider_factories={platform: fn},
        )
        self.assertIs(lib.issue, issue)
        fn.assert_called_once()

    def test_youtrack_issue_exposed(self) -> None:
        self._assert_issue_set(Platform.YOUTRACK)

    def test_jira_issue_exposed(self) -> None:
        self._assert_issue_set(Platform.JIRA)

    def test_github_issue_exposed(self) -> None:
        self._assert_issue_set(Platform.GITHUB)

    def test_github_issues_issue_exposed(self) -> None:
        self._assert_issue_set(Platform.GITHUB_ISSUES)

    def test_gitlab_issue_exposed(self) -> None:
        self._assert_issue_set(Platform.GITLAB)

    def test_gitlab_issues_issue_exposed(self) -> None:
        self._assert_issue_set(Platform.GITLAB_ISSUES)

    def test_bitbucket_issue_exposed(self) -> None:
        self._assert_issue_set(Platform.BITBUCKET)

    def test_bitbucket_issues_issue_exposed(self) -> None:
        self._assert_issue_set(Platform.BITBUCKET_ISSUES)


# ---------------------------------------------------------------------------
# Config and max_retries passthrough
# ---------------------------------------------------------------------------


class TaskCoreLibConfigTests(unittest.TestCase):
    def test_config_passed_through_to_factory(self) -> None:
        captured = {}

        def factory(cfg, max_retries):
            captured['cfg'] = cfg
            return MagicMock()

        cfg = _minimal_cfg(token='special')
        TaskCoreLib(
            Platform.YOUTRACK, cfg, 5,
            provider_factories={Platform.YOUTRACK: factory},
        )
        self.assertIs(captured['cfg'], cfg)

    def test_max_retries_passed_through_to_factory(self) -> None:
        captured = {}

        def factory(cfg, max_retries):
            captured['max_retries'] = max_retries
            return MagicMock()

        TaskCoreLib(
            Platform.JIRA, _minimal_cfg(), 42,
            provider_factories={Platform.JIRA: factory},
        )
        self.assertEqual(captured['max_retries'], 42)

    def test_unknown_platform_sets_issue_to_none(self) -> None:
        lib = TaskCoreLib(
            Platform.YOUTRACK, _minimal_cfg(), 1,
            provider_factories={},  # YOUTRACK not registered
        )
        self.assertIsNone(lib.issue)


# ---------------------------------------------------------------------------
# provider_factories parameter wiring
# ---------------------------------------------------------------------------


class TaskCoreLibFactoryWiringTests(unittest.TestCase):
    def test_provider_factories_kwarg_only(self) -> None:
        fn, issue = _provider()
        # Must be passed as a keyword argument.
        lib = TaskCoreLib(
            Platform.YOUTRACK, _minimal_cfg(), 1,
            provider_factories={Platform.YOUTRACK: fn},
        )
        self.assertIs(lib.issue, issue)

    def test_two_providers_each_platform_gets_own_factory(self) -> None:
        fn_yt, issue_yt = _provider()
        fn_jira, issue_jira = _provider()

        lib_yt = TaskCoreLib(
            Platform.YOUTRACK, _minimal_cfg(), 1,
            provider_factories={
                Platform.YOUTRACK: fn_yt,
                Platform.JIRA: fn_jira,
            },
        )
        lib_jira = TaskCoreLib(
            Platform.JIRA, _minimal_cfg(), 1,
            provider_factories={
                Platform.YOUTRACK: fn_yt,
                Platform.JIRA: fn_jira,
            },
        )

        self.assertIs(lib_yt.issue, issue_yt)
        self.assertIs(lib_jira.issue, issue_jira)
        fn_yt.assert_called_once()
        fn_jira.assert_called_once()


if __name__ == '__main__':
    unittest.main()

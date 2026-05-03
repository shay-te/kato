import unittest


from hydra.core.config_search_path import ConfigSearchPath
from hydra_plugins.kato.kato_searchpath import (
    KatoSearchPathPlugin,
)


class _SearchPath(ConfigSearchPath):
    def __init__(self) -> None:
        self.calls = []

    def append(self, provider: str, path: str) -> None:
        self.calls.append((provider, path))

    def prepend(self, provider: str, path: str) -> None:
        self.calls.insert(0, (provider, path))

    def get_path(self):
        return list(self.calls)


class HydraPluginTests(unittest.TestCase):
    def test_registers_kato_config_path(self) -> None:
        plugin = KatoSearchPathPlugin()
        search_path = _SearchPath()

        plugin.manipulate_search_path(search_path)

        self.assertEqual(
            search_path.calls,
            [
                ("kato_core_lib", "pkg://kato_core_lib.config"),
                ("youtrack_core_lib", "pkg://youtrack_core_lib.youtrack_core_lib.config"),
                ("github_core_lib", "pkg://github_core_lib.github_core_lib.config"),
                (
                    "bitbucket_core_lib",
                    "pkg://bitbucket_core_lib.bitbucket_core_lib.config",
                ),
                (
                    "gitlab_core_lib",
                    "pkg://gitlab_core_lib.gitlab_core_lib.config",
                ),
                (
                    "jira_core_lib",
                    "pkg://jira_core_lib.jira_core_lib.config",
                ),
            ],
        )

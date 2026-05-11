import os
from pathlib import Path
import unittest
from unittest.mock import patch

from omegaconf import OmegaConf


REPO_ROOT = Path(__file__).resolve().parents[3]


class GitHubCoreLibConfigTests(unittest.TestCase):
    @staticmethod
    def _config_path() -> Path:
        return (
            REPO_ROOT
            / 'github_core_lib/github_core_lib/config/github_core_lib/github_core_lib.yaml'
        )

    def test_config_includes_max_retries(self) -> None:
        config_text = self._config_path().read_text(encoding='utf-8')

        self.assertIn(
            'github_core_lib:',
            config_text,
        )
        self.assertIn(
            'max_retries: ${oc.decode:${oc.env:GITHUB_CORE_LIB_MAX_RETRIES,"3"}}',
            config_text,
        )

    def test_config_reads_canonical_github_env_vars(self) -> None:
        env = {
            'GITHUB_API_BASE_URL': 'https://github.example/api/v3',
            'GITHUB_API_TOKEN': 'token-value',
            'GITHUB_OWNER': 'owner-value',
            'GITHUB_REPO': 'repo-value',
        }

        with patch.dict(os.environ, env, clear=True):
            cfg = OmegaConf.load(self._config_path())

            self.assertEqual(cfg.github_core_lib.base_url, 'https://github.example/api/v3')
            self.assertEqual(cfg.github_core_lib.token, 'token-value')
            self.assertEqual(cfg.github_core_lib.owner, 'owner-value')
            self.assertEqual(cfg.github_core_lib.repo, 'repo-value')

    def test_core_lib_path_is_resolvable_with_dot_access(self) -> None:
        cfg = OmegaConf.create(
            {
                'core_lib': {
                    'github_core_lib': {
                        'base_url': 'https://api.github.com',
                    },
                },
                'value': '${core_lib.github_core_lib.base_url}',
            }
        )

        self.assertEqual(cfg.value, 'https://api.github.com')

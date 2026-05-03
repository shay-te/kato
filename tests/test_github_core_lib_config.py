from pathlib import Path
import unittest

from omegaconf import OmegaConf


REPO_ROOT = Path(__file__).resolve().parents[1]


class GitHubCoreLibConfigTests(unittest.TestCase):
    def test_config_includes_max_retries(self) -> None:
        config_text = (REPO_ROOT / 'github_core_lib/config/github_core_lib.yaml').read_text(
            encoding='utf-8'
        )

        self.assertIn(
            'core-lib:\n    github-core-lib:',
            config_text,
        )
        self.assertIn(
            'max_retries: ${oc.decode:${oc.env:GITHUB_CORE_LIB_MAX_RETRIES,"3"}}',
            config_text,
        )

    def test_core_lib_path_is_resolvable_with_hyphenated_keys(self) -> None:
        cfg = OmegaConf.create(
            {
                'github_core_lib': {
                    'core-lib': {
                        'github-core-lib': {
                            'base_url': 'https://api.github.com',
                        },
                    },
                },
                'value': '${github_core_lib.core-lib.github-core-lib.base_url}',
            }
        )

        self.assertEqual(cfg.value, 'https://api.github.com')

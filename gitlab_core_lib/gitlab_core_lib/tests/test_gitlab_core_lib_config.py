from pathlib import Path
import unittest

from omegaconf import OmegaConf


REPO_ROOT = Path(__file__).resolve().parents[3]


class GitLabCoreLibConfigTests(unittest.TestCase):
    def test_config_includes_max_retries(self) -> None:
        config_text = (
            REPO_ROOT / 'gitlab_core_lib/gitlab_core_lib/config/gitlab_core_lib.yaml'
        ).read_text(encoding='utf-8')

        self.assertIn('gitlab_core_lib:', config_text)
        self.assertIn(
            'max_retries: ${oc.decode:${oc.env:GITLAB_CORE_LIB_MAX_RETRIES,"3"}}',
            config_text,
        )

    def test_core_lib_path_is_resolvable_with_dot_access(self) -> None:
        cfg = OmegaConf.create(
            {
                'core_lib': {
                    'gitlab_core_lib': {
                        'base_url': 'https://gitlab.example/api/v4',
                    },
                },
                'value': '${core_lib.gitlab_core_lib.base_url}',
            }
        )

        self.assertEqual(cfg.value, 'https://gitlab.example/api/v4')

from pathlib import Path
import unittest

from omegaconf import OmegaConf


REPO_ROOT = Path(__file__).resolve().parents[3]


class JiraCoreLibConfigTests(unittest.TestCase):
    def test_config_includes_max_retries(self) -> None:
        config_text = (
            REPO_ROOT / 'jira_core_lib/jira_core_lib/config/jira_core_lib/jira_core_lib.yaml'
        ).read_text(encoding='utf-8')

        self.assertIn('jira_core_lib:', config_text)
        self.assertIn(
            'max_retries: ${oc.decode:${oc.env:JIRA_CORE_LIB_MAX_RETRIES,"3"}}',
            config_text,
        )

    def test_core_lib_path_is_resolvable_with_dot_access(self) -> None:
        cfg = OmegaConf.create(
            {
                'core_lib': {
                    'jira_core_lib': {
                        'base_url': 'https://company.atlassian.net',
                    },
                },
                'value': '${core_lib.jira_core_lib.base_url}',
            }
        )

        self.assertEqual(cfg.value, 'https://company.atlassian.net')

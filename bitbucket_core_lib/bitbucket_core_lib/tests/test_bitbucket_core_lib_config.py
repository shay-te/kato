from pathlib import Path
import unittest

from omegaconf import OmegaConf


REPO_ROOT = Path(__file__).resolve().parents[3]


class BitbucketCoreLibConfigTests(unittest.TestCase):
    def test_config_includes_max_retries(self) -> None:
        config_text = (
            REPO_ROOT / 'bitbucket_core_lib/bitbucket_core_lib/config/bitbucket_core_lib.yaml'
        ).read_text(encoding='utf-8')

        self.assertIn(
            'bitbucket_core_lib:',
            config_text,
        )
        self.assertIn(
            'max_retries: ${oc.decode:${oc.env:BITBUCKET_CORE_LIB_MAX_RETRIES,"3"}}',
            config_text,
        )

    def test_core_lib_path_is_resolvable_with_hyphenated_keys(self) -> None:
        cfg = OmegaConf.create(
            {
                'core_lib': {
                    'bitbucket_core_lib': {
                        'base_url': 'https://api.bitbucket.org/2.0',
                    },
                },
                'value': '${core_lib.bitbucket_core_lib.base_url}',
            }
        )

        self.assertEqual(cfg.value, 'https://api.bitbucket.org/2.0')

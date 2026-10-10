"""The lib's composition and its default config."""

from __future__ import annotations

import importlib
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from omegaconf import OmegaConf

from azure_devops_core_lib.azure_devops_core_lib.azure_devops_core_lib import AzureDevOpsCoreLib
from azure_devops_core_lib.azure_devops_core_lib.client.azure_devops_client import (
    AzureDevOpsClient,
)

_CONFIG = (
    Path(__file__).resolve().parents[1]
    / 'config' / 'azure_devops_core_lib' / 'azure_devops_core_lib.yaml'
)


def _config(**environment) -> object:
    """The shipped yaml, resolved against ``environment``, under ``core_lib``."""
    with patch.dict(os.environ, environment, clear=False):
        for key in ('AZURE_API_BASE_URL', 'AZURE_API_TOKEN', 'AZURE_USERNAME',
                    'AZURE_DEVOPS_CORE_LIB_MAX_RETRIES'):
            if key not in environment:
                os.environ.pop(key, None)
        loaded = OmegaConf.create({'core_lib': OmegaConf.load(_CONFIG)})
        return OmegaConf.create(OmegaConf.to_container(loaded, resolve=True))


class ConfigTests(unittest.TestCase):

    def test_defaults_reach_azure_devops_services(self) -> None:
        azure = _config().core_lib.azure_devops_core_lib
        self.assertEqual(
            (azure.base_url, azure.token, azure.username, azure.max_retries),
            ('https://dev.azure.com', '', '', 3),
        )

    def test_the_environment_overrides_each_value(self) -> None:
        azure = _config(
            AZURE_API_BASE_URL='https://tfs.corp', AZURE_API_TOKEN='pat',
            AZURE_USERNAME='bot@corp', AZURE_DEVOPS_CORE_LIB_MAX_RETRIES='5',
        ).core_lib.azure_devops_core_lib
        self.assertEqual(
            (azure.base_url, azure.token, azure.username, azure.max_retries),
            ('https://tfs.corp', 'pat', 'bot@corp', 5),
        )

    def test_the_config_package_imports_cleanly(self) -> None:
        self.assertIsNotNone(importlib.import_module('azure_devops_core_lib.azure_devops_core_lib.config'))


class CompositionTests(unittest.TestCase):

    def test_it_exposes_the_pull_request_client_from_its_config(self) -> None:
        lib = AzureDevOpsCoreLib(_config(AZURE_API_TOKEN='pat', AZURE_DEVOPS_CORE_LIB_MAX_RETRIES='4'))
        self.assertIsInstance(lib.pull_request, AzureDevOpsClient)
        self.assertEqual(lib.pull_request.base_url, 'https://dev.azure.com')
        self.assertEqual(lib.pull_request.max_retries, 4)
        self.assertFalse(hasattr(lib, 'issue'))  # Azure Boards is not provided


if __name__ == '__main__':
    unittest.main()

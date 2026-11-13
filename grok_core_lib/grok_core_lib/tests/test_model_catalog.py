"""Grok models, read from ``grok models``."""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from grok_core_lib.grok_core_lib.helpers import model_catalog
from grok_core_lib.grok_core_lib.helpers.model_catalog import (
    FALLBACK_MODELS,
    discover_models,
    discover_pinned_models,
    reset_models_cache,
)

_RUN = 'grok_core_lib.grok_core_lib.helpers.model_catalog.subprocess.run'
_OUTPUT = '''You are logged in with x.ai.
Default model: grok-4.5
Available models:
  * grok-4.5 (default)
  - grok-4.6
  - custom-model
'''


class DiscoverModelsTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_models_cache()
        self.addCleanup(reset_models_cache)

    def test_the_listed_models_with_the_default_marked(self) -> None:
        with patch(_RUN, return_value=subprocess.CompletedProcess([], 0, _OUTPUT, '')):
            models = discover_models()
        self.assertEqual(models[0], {'id': 'grok-4.5', 'label': 'Grok 4.5', 'default': True})
        self.assertEqual(models[1], {'id': 'grok-4.6', 'label': 'Grok 4.6'})
        self.assertEqual(models[2]['label'], 'custom-model')

    def test_the_result_is_cached_until_forced(self) -> None:
        with patch(_RUN, return_value=subprocess.CompletedProcess([], 0, _OUTPUT, '')) as run:
            discover_models()
            discover_models()
            discover_models(force=True)
        self.assertEqual(run.call_count, 2)

    def test_a_failure_or_unparsable_output_falls_back(self) -> None:
        for outcome in (OSError('missing'), subprocess.CompletedProcess([], 1, '', 'x'),
                        subprocess.CompletedProcess([], 0, 'You are not authenticated.', '')):
            with self.subTest(outcome=outcome):
                reset_models_cache()
                kwargs = {'side_effect': outcome} if isinstance(outcome, Exception) else {'return_value': outcome}
                with patch(_RUN, **kwargs):
                    self.assertEqual(discover_models(), [dict(m) for m in FALLBACK_MODELS])

    def test_the_binary_comes_from_the_host_when_named(self) -> None:
        with patch.dict('os.environ', {model_catalog._BINARY_ENV_KEY: '/opt/grok'}), \
                patch(_RUN, return_value=subprocess.CompletedProcess([], 0, _OUTPUT, '')) as run:
            discover_models()
        self.assertEqual(run.call_args.args[0][0], '/opt/grok')

    def test_no_pinned_versions(self) -> None:
        self.assertEqual(discover_pinned_models(force=True), [])
        self.assertIsNone(model_catalog._parse_models('Available models:\n   '))


if __name__ == '__main__':  # pragma: no cover
    unittest.main()

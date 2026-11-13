"""OpenCode models, read from ``opencode models``."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from opencode_core_lib.opencode_core_lib.helpers import model_catalog
from opencode_core_lib.opencode_core_lib.helpers.model_catalog import (
    FALLBACK_MODELS,
    discover_models,
    discover_pinned_models,
    reset_models_cache,
)


class DiscoverModelsTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_models_cache()
        self.addCleanup(reset_models_cache)

    def test_every_listed_provider_model(self) -> None:
        listing = 'anthropic/claude-sonnet-4-5\nopenai/gpt-5\n\nINFO noise line\n'
        with patch.object(model_catalog, 'run_store_command', return_value=listing):
            self.assertEqual(discover_models(), [
                {'id': 'anthropic/claude-sonnet-4-5', 'label': 'anthropic/claude-sonnet-4-5'},
                {'id': 'openai/gpt-5', 'label': 'openai/gpt-5'},
            ])

    def test_cached_until_forced_and_a_fallback_when_nothing_is_listed(self) -> None:
        with patch.object(model_catalog, 'run_store_command', return_value='') as run:
            self.assertEqual(discover_models(), [dict(m) for m in FALLBACK_MODELS])
            discover_models()
            discover_models(force=True)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(discover_pinned_models(force=True), [])


if __name__ == '__main__':  # pragma: no cover
    unittest.main()

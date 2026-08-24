"""Which backend answers a catalog question — and what happens when it can't.

These pickers are operator-facing: a model list that fails to render is worse
than a stale-but-sane one, so every path here ends in a populated list. The
routing itself used to be a ``'codex' in binary`` test inside a Flask route,
which sent every unrecognised backend down the Claude path without saying so.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from agent_backend_core_lib.agent_backend_core_lib.client.model_catalog_factory import (
    discover_effort_levels,
    discover_models,
    fallback_models,
    platform_for_binary,
)
from agent_backend_core_lib.agent_backend_core_lib.platform import AgentPlatform

_CATALOG = ('agent_backend_core_lib.agent_backend_core_lib.client'
            '.model_catalog_factory')


class PlatformForBinaryTests(unittest.TestCase):
    def test_a_bare_name_resolves(self) -> None:
        self.assertIs(platform_for_binary('codex'), AgentPlatform.CODEX)
        self.assertIs(platform_for_binary('claude'), AgentPlatform.CLAUDE)

    def test_a_full_path_resolves_on_the_containing_name(self) -> None:
        # Operators configure a path, not a platform name.
        self.assertIs(
            platform_for_binary('/usr/local/bin/codex'), AgentPlatform.CODEX,
        )
        self.assertIs(
            platform_for_binary('/opt/homebrew/bin/claude'), AgentPlatform.CLAUDE,
        )

    def test_case_and_padding_do_not_matter(self) -> None:
        self.assertIs(platform_for_binary('  CODEX  '), AgentPlatform.CODEX)

    def test_an_unknown_or_missing_binary_falls_back_to_claude(self) -> None:
        for value in ('', None, 'some-other-agent'):
            self.assertIs(platform_for_binary(value), AgentPlatform.CLAUDE, value)


class DiscoverModelsTests(unittest.TestCase):
    def test_codex_is_asked_for_codex_models(self) -> None:
        with patch('codex_core_lib.codex_core_lib.helpers.model_catalog'
                   '.discover_models',
                   return_value=[{'id': 'gpt-x', 'label': 'GPT X'}]) as codex:
            models = discover_models(AgentPlatform.CODEX)

        codex.assert_called_once_with(force=False)
        self.assertEqual(models, [{'id': 'gpt-x', 'label': 'GPT X'}])

    def test_claude_is_asked_for_claude_models_and_force_passes_through(self) -> None:
        with patch('claude_core_lib.claude_core_lib.helpers.model_catalog'
                   '.discover_models',
                   return_value=[{'id': 'opus', 'label': 'Opus'}]) as claude:
            models = discover_models(AgentPlatform.CLAUDE, force=True)

        claude.assert_called_once_with(force=True)
        self.assertEqual(models, [{'id': 'opus', 'label': 'Opus'}])

    def test_a_platform_name_string_is_accepted(self) -> None:
        with patch('codex_core_lib.codex_core_lib.helpers.model_catalog'
                   '.discover_models', return_value=[{'id': 'x'}]) as codex:
            discover_models('codex-cli')

        codex.assert_called_once()

    def test_a_failing_backend_still_returns_a_usable_list(self) -> None:
        with patch('codex_core_lib.codex_core_lib.helpers.model_catalog'
                   '.discover_models', side_effect=RuntimeError('no cache')):
            models = discover_models(AgentPlatform.CODEX)

        self.assertTrue(models)
        self.assertEqual(models, fallback_models(AgentPlatform.CODEX))

    def test_an_empty_result_falls_back_rather_than_rendering_nothing(self) -> None:
        with patch('claude_core_lib.claude_core_lib.helpers.model_catalog'
                   '.discover_models', return_value=[]):
            self.assertTrue(discover_models(AgentPlatform.CLAUDE))

    def test_the_returned_dicts_are_copies(self) -> None:
        original = [{'id': 'opus'}]
        with patch('claude_core_lib.claude_core_lib.helpers.model_catalog'
                   '.discover_models', return_value=original):
            models = discover_models(AgentPlatform.CLAUDE)

        models[0]['id'] = 'mutated'
        self.assertEqual(original[0]['id'], 'opus')


class DiscoverEffortLevelsTests(unittest.TestCase):
    def test_claude_is_asked_with_its_binary(self) -> None:
        with patch('claude_core_lib.claude_core_lib.helpers.effort_levels'
                   '.discover_effort_levels', return_value=('low', 'high')) as probe:
            levels = discover_effort_levels(AgentPlatform.CLAUDE, '/bin/claude')

        probe.assert_called_once_with('/bin/claude')
        self.assertEqual(levels, ('low', 'high'))

    def test_a_backend_without_discovery_gets_the_shared_fallback(self) -> None:
        # Codex takes an effort setting but publishes no discovery for it. An
        # empty picker would read as "no effort control", which is wrong.
        levels = discover_effort_levels(AgentPlatform.CODEX)

        self.assertIn('high', levels)
        self.assertTrue(levels)

    def test_a_failing_probe_falls_back(self) -> None:
        with patch('claude_core_lib.claude_core_lib.helpers.effort_levels'
                   '.discover_effort_levels', side_effect=OSError('no binary')):
            self.assertTrue(discover_effort_levels(AgentPlatform.CLAUDE))

    def test_an_empty_probe_result_falls_back(self) -> None:
        with patch('claude_core_lib.claude_core_lib.helpers.effort_levels'
                   '.discover_effort_levels', return_value=()):
            self.assertTrue(discover_effort_levels(AgentPlatform.CLAUDE))


if __name__ == '__main__':
    unittest.main()

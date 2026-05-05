"""Unit tests for the platform resolver + factory plumbing.

We don't construct real backends here (those have heavy
dependencies — Claude pulls in the streaming subprocess machinery
+ Anthropic auth, OpenHands needs a live HTTP service). We do
exercise the alias resolution + the lazy-import / dispatch logic.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from agent_core_lib.agent_core_lib.client.agent_client_factory import (
    AgentClientFactory,
    resolve_platform,
)
from agent_core_lib.agent_core_lib.platform import AgentPlatform


class ResolvePlatformTests(unittest.TestCase):
    def test_canonical_names_map_one_to_one(self) -> None:
        self.assertEqual(resolve_platform('claude'), AgentPlatform.CLAUDE)
        self.assertEqual(resolve_platform('openhands'), AgentPlatform.OPENHANDS)

    def test_alias_variants_resolve_to_the_same_platform(self) -> None:
        # Operator-supplied strings come from KATO_AGENT_BACKEND;
        # historically each backend has had a couple of common
        # spellings and we accept all of them. Pinning the alias
        # set so a future tweak can't quietly drop one.
        for alias in ('claude', 'claude-code', 'claude_code', 'claude-cli', 'claude_cli'):
            self.assertEqual(resolve_platform(alias), AgentPlatform.CLAUDE, alias)
        for alias in ('openhands', 'open-hands', 'open_hands'):
            self.assertEqual(resolve_platform(alias), AgentPlatform.OPENHANDS, alias)

    def test_blank_input_falls_back_to_openhands_for_historical_compat(self) -> None:
        # The agent_backend field used to be optional and defaulted
        # to OpenHands. Old configs in the wild rely on this.
        self.assertEqual(resolve_platform(''), AgentPlatform.OPENHANDS)

    def test_unknown_backend_raises_with_actionable_message(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            resolve_platform('gpt-4')
        msg = str(ctx.exception)
        self.assertIn('gpt-4', msg)
        # Names every supported value so the operator sees their
        # options without grepping the source.
        self.assertIn('claude', msg)
        self.assertIn('openhands', msg)

    def test_input_is_case_and_whitespace_insensitive(self) -> None:
        self.assertEqual(resolve_platform('  Claude  '), AgentPlatform.CLAUDE)
        self.assertEqual(resolve_platform('OPENHANDS'), AgentPlatform.OPENHANDS)


class FactoryDispatchTests(unittest.TestCase):
    def test_unhandled_platform_raises_clearly(self) -> None:
        # Defensive guard for a future enum addition that forgets
        # to wire a handler. We can't construct an "extra" enum
        # without modifying the class, so we patch ``build`` to
        # cover the fallthrough branch by feeding a non-matching
        # value through the same code path.
        factory = AgentClientFactory(max_retries=1)

        class _FakePlatform:
            pass

        with self.assertRaises(ValueError):
            factory.build(_FakePlatform(), object())  # type: ignore[arg-type]

    def test_claude_dispatch_routes_to_claude_builder(self) -> None:
        factory = AgentClientFactory(max_retries=1)
        with patch.object(factory, '_build_claude', return_value='CLAUDE') as build_claude, \
             patch.object(factory, '_build_openhands', return_value='OH') as build_oh:
            result = factory.build(AgentPlatform.CLAUDE, object())
        self.assertEqual(result, 'CLAUDE')
        build_claude.assert_called_once()
        build_oh.assert_not_called()

    def test_openhands_dispatch_routes_to_openhands_builder(self) -> None:
        factory = AgentClientFactory(max_retries=1)
        with patch.object(factory, '_build_claude', return_value='CLAUDE') as build_claude, \
             patch.object(factory, '_build_openhands', return_value='OH') as build_oh:
            result = factory.build(AgentPlatform.OPENHANDS, object())
        self.assertEqual(result, 'OH')
        build_oh.assert_called_once()
        build_claude.assert_not_called()


if __name__ == '__main__':
    unittest.main()

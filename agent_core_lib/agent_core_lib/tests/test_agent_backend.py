"""One canonical spelling of each backend name.

These strings are persisted in session records, read from operator config, and
compared in libs that cannot import one another. Hand-spelled per site they
drift silently: a typo compares False forever and nothing fails until someone
notices a feature quietly not applying to their backend.
"""

from __future__ import annotations

import json
import unittest

from agent_core_lib.agent_core_lib.data.agent_backend import AgentBackend


class BackendValueTests(unittest.TestCase):
    def test_the_names_are_the_ones_written_to_disk(self) -> None:
        self.assertEqual(AgentBackend.CLAUDE.value, 'claude')
        self.assertEqual(AgentBackend.CODEX.value, 'codex')
        self.assertEqual(AgentBackend.OPENHANDS.value, 'openhands')

    def test_a_member_compares_equal_to_its_string(self) -> None:
        # Records and config hold bare strings; the enum must slot in beside
        # them without every call site converting.
        self.assertEqual(AgentBackend.CLAUDE, 'claude')
        self.assertTrue(AgentBackend.CODEX == 'codex')

    def test_it_serialises_as_the_bare_string(self) -> None:
        # A record written with the enum must be readable by a host that
        # predates it, and vice versa.
        self.assertEqual(json.dumps({'b': AgentBackend.CODEX}), '{"b": "codex"}')


class ParseTests(unittest.TestCase):
    def test_it_reads_the_casing_and_padding_config_actually_contains(self) -> None:
        self.assertIs(AgentBackend.parse('  CODEX '), AgentBackend.CODEX)
        self.assertIs(AgentBackend.parse('Claude'), AgentBackend.CLAUDE)

    def test_an_unknown_name_is_None_not_a_default(self) -> None:
        # Guessing is how a feature silently applies to the wrong CLI.
        for value in ('gpt', '', '   ', None, 0):
            with self.subTest(value=value):
                self.assertIsNone(AgentBackend.parse(value))

    def test_an_enum_member_parses_to_itself(self) -> None:
        self.assertIs(AgentBackend.parse(AgentBackend.CLAUDE), AgentBackend.CLAUDE)


class IsATests(unittest.TestCase):
    def test_it_matches_regardless_of_casing(self) -> None:
        self.assertTrue(AgentBackend.is_a('CLAUDE', AgentBackend.CLAUDE))
        self.assertTrue(AgentBackend.is_a(' codex ', AgentBackend.CODEX))

    def test_it_does_not_match_a_different_backend(self) -> None:
        self.assertFalse(AgentBackend.is_a('codex', AgentBackend.CLAUDE))

    def test_it_never_raises_on_junk(self) -> None:
        for value in (None, 0, [], object()):
            with self.subTest(value=value):
                self.assertFalse(AgentBackend.is_a(value, AgentBackend.CLAUDE))


class FactoryEnumStaysInSyncTests(unittest.TestCase):
    """The client factory keeps its own platform enum; the VALUES are derived."""

    def test_the_factory_platform_values_match_exactly(self) -> None:
        from agent_backend_core_lib.agent_backend_core_lib.platform import (
            AgentPlatform,
        )
        self.assertEqual(
            {p.value for p in AgentPlatform},
            {b.value for b in AgentBackend},
            'the factory platform enum and the canonical backend names have '
            'diverged — one of them is now spelling a backend by hand',
        )

    def test_the_member_names_match_too(self) -> None:
        from agent_backend_core_lib.agent_backend_core_lib.platform import (
            AgentPlatform,
        )
        self.assertEqual({p.name for p in AgentPlatform}, {b.name for b in AgentBackend})


if __name__ == '__main__':
    unittest.main()

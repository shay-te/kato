"""The CLI-transport mirror contract is self-consistent data."""

from __future__ import annotations

import unittest

from agent_provider_contracts.agent_provider_contracts.agent_session import AgentSession
from agent_provider_contracts.agent_provider_contracts.agent_session_manager import (
    AgentSessionManager,
)
from agent_provider_contracts.agent_provider_contracts.cli_transport_layout import (
    AGENT_PLACEHOLDER,
    REQUIRED_FILES,
    REQUIRED_MEMBERS,
    REQUIRED_NAMES,
    REQUIRED_PARAMETERS,
    REQUIRED_SIGNATURES,
    for_agent,
    protocol_members,
)


class LayoutContractTests(unittest.TestCase):
    def test_every_required_module_is_a_required_file(self) -> None:
        files = set(REQUIRED_FILES)
        for dotted in REQUIRED_NAMES:
            relative = '<pkg>/' + (dotted.replace('.', '/') + '.py' if dotted else '__init__.py')
            self.assertIn(relative, files)

    def test_every_signature_names_a_required_function(self) -> None:
        for path in (*REQUIRED_SIGNATURES, *REQUIRED_PARAMETERS):
            module, _, name = path.rpartition('.')
            self.assertIn(name, REQUIRED_NAMES[module])

    def test_every_class_with_members_is_a_required_name(self) -> None:
        names = {name for listed in REQUIRED_NAMES.values() for name in listed}
        self.assertEqual(set(REQUIRED_MEMBERS) - names, set())

    def test_protocol_members_are_the_public_names_in_order(self) -> None:
        self.assertEqual(protocol_members(AgentSessionManager),
                         ('get_session', 'start_session', 'terminate_session', 'shutdown'))
        self.assertEqual(protocol_members(AgentSession)[0], 'task_id')

    def test_the_agent_stem_fills_the_placeholder(self) -> None:
        self.assertEqual(for_agent(f'Streaming{AGENT_PLACEHOLDER}Session', 'Grok'), 'StreamingGrokSession')

    def test_the_manager_protocol_is_runtime_checkable(self) -> None:
        class _Manager:
            def get_session(self, task_id): return None
            def start_session(self, **kwargs): return None
            def terminate_session(self, task_id, **kwargs): return None
            def shutdown(self): return None

        self.assertIsInstance(_Manager(), AgentSessionManager)


if __name__ == '__main__':  # pragma: no cover
    unittest.main()

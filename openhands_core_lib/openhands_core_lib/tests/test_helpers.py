"""Coverage for the small helpers in ``openhands_core_lib/helpers/``."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from openhands_core_lib.openhands_core_lib.helpers.agents_instruction_utils import (
    _repository_section,
    agents_instructions_for_path,
    repository_agents_instructions_text,
)
from openhands_core_lib.openhands_core_lib.helpers.result_utils import (
    openhands_success_flag,
)


class AgentsInstructionsForPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)

    def test_returns_empty_when_workspace_blank(self) -> None:
        self.assertEqual(agents_instructions_for_path(''), '')

    def test_returns_empty_when_workspace_missing(self) -> None:
        self.assertEqual(
            agents_instructions_for_path(str(self.workspace / 'nope')),
            '',
        )

    def test_returns_empty_when_no_agents_md_files(self) -> None:
        # Line 36: no AGENTS.md files anywhere → empty result.
        # Workspace exists but has no AGENTS.md.
        self.assertEqual(agents_instructions_for_path(str(self.workspace)), '')

    def test_returns_section_when_agents_md_present(self) -> None:
        (self.workspace / 'AGENTS.md').write_text('follow these rules', encoding='utf-8')
        result = agents_instructions_for_path(str(self.workspace))
        self.assertIn('follow these rules', result)
        self.assertIn('AGENTS.md', result)


class RepositorySectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)

    def test_returns_empty_when_local_path_blank(self) -> None:
        # Line 61: blank ``local_path`` → ''.
        repo = SimpleNamespace(id='repo', local_path='')
        self.assertEqual(_repository_section(repo), '')

    def test_returns_empty_when_local_path_not_a_dir(self) -> None:
        # ``is_dir`` False → ''.
        repo = SimpleNamespace(id='repo', local_path='/does/not/exist/anywhere')
        self.assertEqual(_repository_section(repo), '')

    def test_returns_empty_when_no_agents_entries(self) -> None:
        # Line 64: no AGENTS.md found → ''.
        repo = SimpleNamespace(id='repo', local_path=str(self.workspace))
        self.assertEqual(_repository_section(repo), '')

    def test_renders_section_when_agents_md_exists(self) -> None:
        (self.workspace / 'AGENTS.md').write_text('rules', encoding='utf-8')
        repo = SimpleNamespace(id='client', local_path=str(self.workspace))
        result = _repository_section(repo)
        self.assertIn('rules', result)
        self.assertIn('client', result)


class RepositoryAgentsInstructionsTextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name)

    def test_returns_empty_for_empty_list(self) -> None:
        self.assertEqual(repository_agents_instructions_text([]), '')

    def test_aggregates_sections_across_repos(self) -> None:
        # Two repos, both with AGENTS.md.
        repo_a = self.workspace / 'a'
        repo_a.mkdir()
        (repo_a / 'AGENTS.md').write_text('rules-a')
        repo_b = self.workspace / 'b'
        repo_b.mkdir()
        (repo_b / 'AGENTS.md').write_text('rules-b')
        result = repository_agents_instructions_text([
            SimpleNamespace(id='aaa', local_path=str(repo_a)),
            SimpleNamespace(id='bbb', local_path=str(repo_b)),
        ])
        self.assertIn('rules-a', result)
        self.assertIn('rules-b', result)


class OpenhandsSuccessFlagTests(unittest.TestCase):
    def test_returns_default_when_payload_not_mapping(self) -> None:
        self.assertFalse(openhands_success_flag(None))
        self.assertFalse(openhands_success_flag('not a mapping'))

    def test_returns_default_when_success_key_missing(self) -> None:
        self.assertFalse(openhands_success_flag({}, default=False))
        self.assertTrue(openhands_success_flag({}, default=True))

    def test_passthrough_for_bool_value(self) -> None:
        from kato_core_lib.data_layers.data.fields import ImplementationFields
        self.assertTrue(openhands_success_flag({ImplementationFields.SUCCESS: True}))
        self.assertFalse(openhands_success_flag({ImplementationFields.SUCCESS: False}))

    def test_string_truthy_values(self) -> None:
        from kato_core_lib.data_layers.data.fields import ImplementationFields
        for v in ('1', 'true', 'TRUE', 'Yes', 'on'):
            self.assertTrue(openhands_success_flag({ImplementationFields.SUCCESS: v}))

    def test_string_falsy_value(self) -> None:
        from kato_core_lib.data_layers.data.fields import ImplementationFields
        self.assertFalse(openhands_success_flag({ImplementationFields.SUCCESS: 'no'}))

    def test_falls_back_to_bool_for_other_types(self) -> None:
        # Line 26: ``return bool(value)`` for non-bool, non-string types.
        from kato_core_lib.data_layers.data.fields import ImplementationFields
        self.assertTrue(openhands_success_flag({ImplementationFields.SUCCESS: 1}))
        self.assertFalse(openhands_success_flag({ImplementationFields.SUCCESS: 0}))
        self.assertTrue(openhands_success_flag({ImplementationFields.SUCCESS: [1]}))
        self.assertFalse(openhands_success_flag({ImplementationFields.SUCCESS: []}))


if __name__ == '__main__':
    unittest.main()

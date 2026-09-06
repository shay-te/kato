"""Remembered Bash approvals must converge.

The decision key was the WHOLE command chain — every program it runs, joined:

    Bash awk grep sed
    Bash . .env . test.env set grep python3 do { break }
    Bash -oE sort sed for do if grep then else fi done echo

So every new COMBINATION of already-approved programs was a brand-new
decision. The store grew combinatorially and never converged: an operator
with 840 remembered entries was still being asked on almost every turn —
"i am still doing 'always approve' on the approval prompts, i expected by now
that he remembers enough patterns".

That is a safety problem as well as an annoyance: a prompt that fires 840
times is one people click through without reading.

Decisions are now remembered PER PROGRAM, which is what the UI has always
promised ("Bash entries are per program — allowing one program never allows
another"). The property the whole-chain key was protecting still holds: one
unknown program makes the whole command ask.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from kato_core_lib.helpers.tool_decision_store import (
    recall_command_decision,
    remember_command_decision,
)
from kato_core_lib.helpers.tool_decision_utils import (
    command_programs_of,
    decision_programs_for,
)


class _Store(unittest.TestCase):
    """Each test gets its own decisions file."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        path = os.path.join(self._tmp.name, 'tool_decisions.json')
        patcher = mock.patch.dict(
            os.environ, {'KATO_TOOL_DECISIONS_PATH': path},
        )
        patcher.start()
        self.addCleanup(patcher.stop)


class ApprovalsConvergeTests(_Store):
    def test_a_NEW_COMBINATION_of_approved_programs_does_not_re_ask(self) -> None:
        # THE REPORT. Approve them separately, then run them together.
        remember_command_decision('Bash', ['awk'], True)
        remember_command_decision('Bash', ['grep'], True)
        self.assertTrue(recall_command_decision('Bash', ['awk', 'grep']))
        self.assertTrue(recall_command_decision('Bash', ['grep', 'awk']))

    def test_approving_a_chain_approves_each_of_its_programs(self) -> None:
        remember_command_decision('Bash', ['mvn', 'docker'], True)
        self.assertTrue(recall_command_decision('Bash', ['mvn']))
        self.assertTrue(recall_command_decision('Bash', ['docker']))

    def test_ONE_unknown_program_still_makes_the_whole_command_ask(self) -> None:
        # The safety property the whole-chain key was protecting. A new
        # program tacked onto an approved one must not ride through.
        remember_command_decision('Bash', ['mvn'], True)
        self.assertIsNone(recall_command_decision('Bash', ['mvn', 'rm']))

    def test_a_DENIED_program_denies_the_whole_command(self) -> None:
        remember_command_decision('Bash', ['mvn'], True)
        remember_command_decision('Bash', ['rm'], False)
        self.assertIs(recall_command_decision('Bash', ['mvn', 'rm']), False)

    def test_an_unknown_program_alone_asks(self) -> None:
        self.assertIsNone(recall_command_decision('Bash', ['never-seen']))

    def test_the_store_does_not_grow_per_combination(self) -> None:
        # The actual defect: N programs used to cost one entry per distinct
        # chain. Now it is one entry per program, whatever they are mixed
        # into.
        from kato_core_lib.helpers.tool_decision_store import read_tool_decisions
        for chain in (['a'], ['a', 'b'], ['b', 'a'], ['a', 'b', 'c'], ['c']):
            remember_command_decision('Bash', chain, True)
        self.assertEqual(len(read_tool_decisions()), 3)  # a, b, c


class LegacyEntriesStillCountTests(_Store):
    """An operator's existing decisions must not silently reset."""

    def _write_legacy(self, mapping):
        import json
        with open(os.environ['KATO_TOOL_DECISIONS_PATH'], 'w') as handle:
            json.dump(mapping, handle)

    def test_a_legacy_chain_ALLOW_counts_for_each_program(self) -> None:
        self._write_legacy({'Bash awk grep sed': 'allow'})
        for program in ('awk', 'grep', 'sed'):
            with self.subTest(program=program):
                self.assertTrue(recall_command_decision('Bash', [program]))

    def test_a_legacy_chain_DENY_is_not_spread_to_its_programs(self) -> None:
        # The operator may have been refusing the `rm`, not the `mvn`.
        self._write_legacy({'Bash mvn rm': 'deny'})
        self.assertIsNone(recall_command_decision('Bash', ['mvn']))
        self.assertIsNone(recall_command_decision('Bash', ['rm']))

    def test_expansion_does_not_rewrite_the_file(self) -> None:
        # Read-side only, so the operator can still drop the file, and a
        # downgrade finds its own keys intact.
        import json
        self._write_legacy({'Bash awk grep': 'allow'})
        recall_command_decision('Bash', ['awk'])
        with open(os.environ['KATO_TOOL_DECISIONS_PATH']) as handle:
            self.assertEqual(json.load(handle), {'Bash awk grep': 'allow'})


class ProgramExtractionTests(unittest.TestCase):
    def test_output_shaping_pipes_are_not_separate_decisions(self) -> None:
        # `... | head -30` this turn and `... | tail -20` next turn are the
        # same decision.
        self.assertEqual(command_programs_of('ls | head -30'), ['ls'])
        self.assertEqual(command_programs_of('ls | tail -20'), ['ls'])

    def test_a_chained_program_is_kept(self) -> None:
        self.assertEqual(command_programs_of('mvn verify && rm -rf /tmp/x'),
                         ['mvn', 'rm'])

    def test_a_non_command_tool_has_no_programs(self) -> None:
        self.assertEqual(decision_programs_for('Edit', {'file_path': '/a'}), [])

    def test_an_empty_command_yields_nothing(self) -> None:
        self.assertEqual(command_programs_of(''), [])


if __name__ == '__main__':
    unittest.main()

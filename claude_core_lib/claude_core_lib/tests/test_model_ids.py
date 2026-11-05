"""A Claude model as a person writes it, as the ``claude`` CLI takes it.

Report: "he works on an older model, Sonnet 4.6 — he types it and is told
there is no such model". The CLI takes ``claude-sonnet-4-6`` or ``sonnet``,
never "sonnet 4.6".
"""

from __future__ import annotations

import unittest

from claude_core_lib.claude_core_lib.helpers.model_ids import cli_model_id, model_flags


class CliModelIdTests(unittest.TestCase):
    def test_a_family_and_a_version_become_the_model_id(self) -> None:
        for written in ('sonnet 4.6', 'Sonnet 4.6', 'SONNET 4.6', 'sonnet:4.6', 'sonnet-4.6',
                        'sonnet 4-6', 'claude sonnet 4.6', 'Claude-Sonnet-4.6', 'sonnet4.6',
                        'sonnet v4.6', ' sonnet  4.6 '):
            with self.subTest(written=written):
                self.assertEqual(cli_model_id(written), 'claude-sonnet-4-6')
        self.assertEqual(cli_model_id('opus 5'), 'claude-opus-5')
        self.assertEqual(cli_model_id('Fable 5.1'), 'claude-fable-5-1')
        self.assertEqual(cli_model_id('haiku 4.05'), 'claude-haiku-4-5')

    def test_a_bare_family_is_its_alias(self) -> None:
        self.assertEqual(cli_model_id('Opus'), 'opus')
        self.assertEqual(cli_model_id('claude sonnet'), 'sonnet')

    def test_what_the_cli_already_takes_is_passed_as_written(self) -> None:
        for model in ('sonnet', 'claude-sonnet-4-6', 'claude-haiku-4-5-20251001',
                      'claude-opus-5-5[1m]', 'claude-3-5-sonnet-20241022',
                      'us.anthropic.claude-sonnet-4-6-v1:0', 'gpt-5-codex'):
            with self.subTest(model=model):
                self.assertEqual(cli_model_id(model), model)
        self.assertEqual(cli_model_id('  claude-opus-4-8 '), 'claude-opus-4-8')
        self.assertEqual(cli_model_id(None), '')

    def test_the_flag_is_there_only_when_a_model_was_asked_for(self) -> None:
        self.assertEqual(model_flags('Sonnet 4.6'), ['--model', 'claude-sonnet-4-6'])
        self.assertEqual(model_flags(''), [])
        self.assertEqual(model_flags('   '), [])
        self.assertEqual(model_flags(None), [])


if __name__ == '__main__':
    unittest.main()

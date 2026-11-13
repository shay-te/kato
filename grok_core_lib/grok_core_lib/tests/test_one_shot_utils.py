"""Grok's text-in, text-out one-shot."""

from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch

from grok_core_lib.grok_core_lib.helpers.one_shot_utils import (
    OneShotError,
    make_one_shot,
    one_shot,
)

_RUN = 'agent_core_lib.agent_core_lib.helpers.one_shot.subprocess.run'


def _reply(text: str, *, error: bool = False) -> subprocess.CompletedProcess:
    lines = ['a banner line', {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': text}]}}]
    lines.append({'type': 'result', 'is_error': error, 'subtype': 'error_during_execution' if error else 'success',
                  'errors': [text] if error else []})
    rendered = [line if isinstance(line, str) else json.dumps(line) for line in lines]
    return subprocess.CompletedProcess([], 0, '\n'.join(rendered), '')


class OneShotTests(unittest.TestCase):
    def test_the_prompt_goes_in_a_file_and_the_reply_comes_back(self) -> None:
        seen = {}

        def fake_run(argv, **kwargs):
            seen['argv'] = argv
            seen['prompt'] = open(argv[argv.index('--prompt-file') + 1], encoding='utf-8').read()
            return _reply('the answer')

        with patch(_RUN, side_effect=fake_run):
            self.assertEqual(one_shot('question?', model='grok-4.6', cwd='/tmp'), 'the answer')
        self.assertEqual(seen['prompt'], 'question?')
        self.assertIn('--no-subagents', seen['argv'])
        self.assertEqual(seen['argv'][seen['argv'].index('-m') + 1], 'grok-4.6')
        self.assertIn('Edit', seen['argv'])

    def test_a_refused_turn_is_an_error(self) -> None:
        with patch(_RUN, return_value=_reply('quota exceeded', error=True)):
            with self.assertRaises(OneShotError) as caught:
                one_shot('q')
        self.assertIn('quota exceeded', str(caught.exception))

    def test_make_one_shot_binds_its_config(self) -> None:
        with patch(_RUN, return_value=_reply('bound')) as run:
            self.assertEqual(make_one_shot(binary='/opt/grok')('q'), 'bound')
        self.assertTrue(run.call_args.args[0][0].endswith('grok'))


if __name__ == '__main__':  # pragma: no cover
    unittest.main()

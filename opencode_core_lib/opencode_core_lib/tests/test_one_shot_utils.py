"""OpenCode's text-in, text-out one-shot."""

from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch

from opencode_core_lib.opencode_core_lib.helpers import one_shot_utils
from opencode_core_lib.opencode_core_lib.helpers.cli_generation import GENERATION_V2
from opencode_core_lib.opencode_core_lib.helpers.one_shot_utils import (
    OneShotError,
    make_one_shot,
    one_shot,
)

_RUN = 'agent_core_lib.agent_core_lib.helpers.one_shot.subprocess.run'


def _reply(*lines) -> subprocess.CompletedProcess:
    text = '\n'.join(line if isinstance(line, str) else json.dumps(line) for line in lines)
    return subprocess.CompletedProcess([], 0, text, '')


class OneShotTests(unittest.TestCase):
    def test_the_prompt_goes_on_stdin_with_read_only_rules(self) -> None:
        with patch.object(one_shot_utils, 'cli_generation', return_value=GENERATION_V2), \
                patch(_RUN, return_value=_reply('banner', {'type': 'text', 'part': {'text': 'answer'}})) as run:
            self.assertEqual(one_shot('question?', model='openai/gpt-5', cwd='/tmp'), 'answer')
        self.assertEqual(run.call_args.kwargs['input'], 'question?')
        argv = run.call_args.args[0]
        self.assertIn('--standalone', argv)
        self.assertEqual(argv[argv.index('-m') + 1], 'openai/gpt-5')
        config = json.loads(run.call_args.kwargs['env']['OPENCODE_CONFIG_CONTENT'])
        self.assertEqual(config['permission']['edit'], 'deny')

    def test_an_error_line_is_an_error(self) -> None:
        with patch.object(one_shot_utils, 'cli_generation', return_value=GENERATION_V2), \
                patch(_RUN, return_value=_reply({'type': 'error', 'error': {'message': 'no key'}})):
            with self.assertRaises(OneShotError) as caught:
                one_shot('q')
        self.assertIn('no key', str(caught.exception))

    def test_make_one_shot_binds_its_config(self) -> None:
        with patch.object(one_shot_utils, 'cli_generation', return_value=GENERATION_V2), \
                patch(_RUN, return_value=_reply({'type': 'text', 'part': {'text': 'bound'}})):
            self.assertEqual(make_one_shot(binary='opencode')('q'), 'bound')


if __name__ == '__main__':  # pragma: no cover
    unittest.main()

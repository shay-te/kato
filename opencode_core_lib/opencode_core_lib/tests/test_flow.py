"""A-Z flows through ``opencode_core_lib``, the way a host drives it.

1. The one-shot client: health check → implement a task → fix a review
   comment → a read-only investigation. ``subprocess.run`` is the one double,
   answering with the JSON lines a real ``opencode run --format json`` prints
   (and no done event — success is a clean exit).
2. A chat: the manager starts a real ``StreamingOpenCodeSession`` against a
   fake ``opencode`` executable; the record is written with the learned
   session id, the second turn resumes it, and a restart's record resumes the
   same conversation.

This lib is standalone: it depends on no backend-selector layer above it.
"""

from __future__ import annotations

import json
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_core_lib.agent_core_lib.data.fields import ImplementationFields
from agent_core_lib.agent_core_lib.session.record import AgentSessionRecord
from opencode_core_lib.opencode_core_lib import OpenCodeCliClient, OpenCodeSessionManager
from opencode_core_lib.opencode_core_lib.helpers.cli_generation import (
    reset_cli_generation_cache,
)
from provider_client_base.provider_client_base.data.review_comment import ReviewComment

_RUN = 'opencode_core_lib.opencode_core_lib.cli_client.subprocess.run'
_WHICH = 'opencode_core_lib.opencode_core_lib.cli_client.shutil.which'


def _opencode_output(text: str) -> str:
    return '\n'.join(json.dumps(line) for line in (
        {'type': 'step_start', 'sessionID': 'ses_flow', 'part': {}},
        {'type': 'tool_use', 'sessionID': 'ses_flow',
         'part': {'tool': 'edit', 'state': {'status': 'completed', 'input': {'filePath': 'app.py'}}}},
        {'type': 'text', 'sessionID': 'ses_flow', 'part': {'type': 'text', 'text': text}},
        {'type': 'step_finish', 'sessionID': 'ses_flow', 'part': {'tokens': {'input': 5}}},
    ))


class OneShotClientFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_cli_generation_cache()
        self.addCleanup(reset_cli_generation_cache)

    def test_health_check_implement_review_and_investigate(self) -> None:
        calls = []

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            if argv[-1] == '--version':
                return subprocess.CompletedProcess(argv, 0, '2.2.7', '')
            if argv[-1] == '--help':
                return subprocess.CompletedProcess(argv, 0, '--format json', '')
            return subprocess.CompletedProcess(argv, 0, _opencode_output(f'handled {len(kwargs["input"])}'), '')

        client = OpenCodeCliClient(model='anthropic/claude-sonnet-4-5')
        task = SimpleNamespace(id='T-7', summary='Add a flag', description='Add --dry-run', tags=[])
        comment = ReviewComment(pull_request_id='17', comment_id='c1', author='r', body='rename this')
        # One double for every spawn, the generation probe's ``--version`` included.
        with patch(_WHICH, return_value='/usr/local/bin/opencode'), patch(_RUN, side_effect=fake_run):
            client.validate_connection()
            implemented = client.implement_task(task)
            fixed = client.fix_review_comment(comment, 'T-7')
            answer = client.investigate('what changed?', cwd='/work')

        self.assertTrue(implemented[ImplementationFields.SUCCESS])
        self.assertEqual(implemented[ImplementationFields.AGENT_SESSION_ID], 'ses_flow')
        self.assertTrue(fixed[ImplementationFields.SUCCESS])
        self.assertTrue(answer.startswith('handled'))
        argv, kwargs = calls[-1]
        self.assertIn('--standalone', argv)
        rules = json.loads(kwargs['env']['OPENCODE_CONFIG_CONTENT'])['permission']
        self.assertEqual(rules['edit'], 'deny')  # the investigation was read-only
        self.assertEqual(kwargs['env']['PWD'], '/work')


_FAKE_OPENCODE = '''\
#!{python}
import json, sys
args = sys.argv[1:]
if args == ['--version']:
    print('2.2.7')
    sys.exit(0)
open({log!r}, 'a').write(json.dumps(args) + '\\n')
prompt = sys.stdin.read().strip()
session = args[args.index('--session') + 1] if '--session' in args else 'ses_chat'
print(json.dumps({{"type": "step_start", "sessionID": session, "part": {{}}}}))
print(json.dumps({{"type": "text", "sessionID": session, "part": {{"text": "re: " + prompt[-10:]}}}}))
'''


def _wait(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@unittest.skipIf(sys.platform.startswith('win'), 'the fake CLI is a shebang script')
class ChatFlowTests(unittest.TestCase):
    def test_a_chat_learns_its_id_resumes_and_survives_a_restart(self) -> None:
        reset_cli_generation_cache()
        self.addCleanup(reset_cli_generation_cache)
        folder = Path(tempfile.mkdtemp())
        log = folder / 'argv.jsonl'
        binary = folder / 'opencode'
        binary.write_text(_FAKE_OPENCODE.format(python=sys.executable, log=str(log)), encoding='utf-8')
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
        records: dict[str, AgentSessionRecord] = {}

        def keep(record):
            records[record.task_id] = record

        manager = OpenCodeSessionManager(
            state_dir=str(folder / 'state'), record_sink=keep, record_source=records.get,
        )
        self.addCleanup(manager.shutdown)
        session = manager.start_session(
            task_id='T-9', cwd=str(folder), binary=str(binary), initial_prompt='first question',
        )
        self.assertTrue(_wait(lambda: session.agent_session_id == 'ses_chat' and not session.is_working))
        self.assertTrue(_wait(lambda: records['T-9'].agent_session_id == 'ses_chat'))
        self.assertEqual(session.terminal_event.event_type, 'turn.completed')

        session.send_user_message('second question')
        self.assertTrue(_wait(lambda: len(log.read_text().splitlines()) == 2 and not session.is_working))
        second = json.loads(log.read_text().splitlines()[1])
        self.assertEqual(second[second.index('--session') + 1], 'ses_chat')

        manager.terminate_session('T-9')
        self.assertIsNone(manager.get_session('T-9'))
        revived = manager.start_session(task_id='T-9', cwd=str(folder), binary=str(binary))
        self.assertEqual(revived.agent_session_id, 'ses_chat')


if __name__ == '__main__':  # pragma: no cover
    unittest.main()

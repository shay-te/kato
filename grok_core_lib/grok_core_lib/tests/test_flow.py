"""A-Z flows through ``grok_core_lib``, the way a host drives it.

1. The one-shot client: health check → implement a task → fix a review
   comment → a read-only investigation. ``subprocess.run`` is the one double,
   answering with the JSONL a real ``grok -p`` prints.
2. A chat: the manager starts a real ``StreamingGrokSession`` against a fake
   ``grok`` executable that parses the real argv; the record is written with
   the learned session id, the second turn resumes it, and a restart's record
   resumes the same conversation.

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
from grok_core_lib.grok_core_lib import GrokCliClient, GrokSessionManager
from provider_client_base.provider_client_base.data.review_comment import ReviewComment

_RUN = 'grok_core_lib.grok_core_lib.cli_client.subprocess.run'
_WHICH = 'grok_core_lib.grok_core_lib.cli_client.shutil.which'


def _grok_output(text: str, session_id: str = 'sess-flow') -> str:
    return '\n'.join(json.dumps(line) for line in (
        {'type': 'system', 'subtype': 'init', 'session_id': session_id},
        {'type': 'assistant', 'message': {'content': [
            {'type': 'tool_use', 'name': 'edit_file', 'input': {'target_file': 'app.py'}},
            {'type': 'text', 'text': text}]}},
        {'type': 'result', 'subtype': 'success', 'is_error': False, 'result': text},
    ))


class OneShotClientFlowTests(unittest.TestCase):
    def test_health_check_implement_review_and_investigate(self) -> None:
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            if argv[-1] == '--version':
                return subprocess.CompletedProcess(argv, 0, 'grok 1.0.45', '')
            if argv[-1] == '--help':
                return subprocess.CompletedProcess(argv, 0, 'streaming-messages-json', '')
            prompt = Path(argv[argv.index('--prompt-file') + 1]).read_text(encoding='utf-8')
            return subprocess.CompletedProcess(argv, 0, _grok_output(f'handled {len(prompt)}'), '')

        client = GrokCliClient(model='grok-4.6')
        task = SimpleNamespace(id='T-7', summary='Add a flag', description='Add --dry-run', tags=[])
        comment = ReviewComment(
            pull_request_id='17', comment_id='c1', author='reviewer', body='rename this',
        )
        with patch(_WHICH, return_value='/usr/local/bin/grok'), patch(_RUN, side_effect=fake_run):
            client.validate_connection()
            implemented = client.implement_task(task)
            fixed = client.fix_review_comment(comment, 'T-7')
            answer = client.investigate('what changed?', cwd='/work')

        self.assertTrue(implemented[ImplementationFields.SUCCESS])
        self.assertEqual(implemented[ImplementationFields.AGENT_SESSION_ID], 'sess-flow')
        self.assertTrue(fixed[ImplementationFields.SUCCESS])
        self.assertTrue(answer.startswith('handled'))
        run_argv = calls[-1]
        self.assertIn('read-only', run_argv)
        self.assertIn('Bash(git push*)', run_argv)
        self.assertEqual(run_argv[run_argv.index('-m') + 1], 'grok-4.6')


_FAKE_GROK = '''\
#!{python}
import json, sys
args = sys.argv[1:]
def value(flag):
    return args[args.index(flag) + 1] if flag in args else ''
open({log!r}, 'a').write(json.dumps(args) + '\\n')
prompt = open(value('--prompt-file')).read().strip()
session = value('--resume') or 'sess-chat'
print(json.dumps({{"type": "system", "subtype": "init", "session_id": session}}))
print(json.dumps({{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "re: " + prompt[-10:]}}]}}}}))
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False}}))
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
        folder = Path(tempfile.mkdtemp())
        log = folder / 'argv.jsonl'
        binary = folder / 'grok'
        binary.write_text(_FAKE_GROK.format(python=sys.executable, log=str(log)), encoding='utf-8')
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
        records: dict[str, AgentSessionRecord] = {}

        def keep(record):
            records[record.task_id] = record

        manager = GrokSessionManager(
            state_dir=str(folder / 'state'), record_sink=keep, record_source=records.get,
        )
        self.addCleanup(manager.shutdown)
        session = manager.start_session(
            task_id='T-9', cwd=str(folder), binary=str(binary), initial_prompt='first question',
        )
        self.assertTrue(_wait(lambda: session.agent_session_id == 'sess-chat' and not session.is_working))
        self.assertTrue(_wait(lambda: records['T-9'].agent_session_id == 'sess-chat'))
        self.assertIs(manager.get_session('T-9'), session)

        session.send_user_message('second question')
        self.assertTrue(_wait(lambda: len(log.read_text().splitlines()) == 2 and not session.is_working))
        second = json.loads(log.read_text().splitlines()[1])
        self.assertEqual(second[second.index('--resume') + 1], 'sess-chat')

        # A restart: the live chat is gone, the record remains.
        manager.terminate_session('T-9')
        self.assertIsNone(manager.get_session('T-9'))
        revived = manager.start_session(task_id='T-9', cwd=str(folder), binary=str(binary))
        self.assertEqual(revived.agent_session_id, 'sess-chat')


if __name__ == '__main__':  # pragma: no cover
    unittest.main()

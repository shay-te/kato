"""A Grok chat against a fake ``grok`` executable that parses the real argv.

The fake reads ``--prompt-file`` and ``--resume`` exactly as Grok does and
prints ``streaming-messages-json`` lines, so these tests exercise the real
command the session builds, the prompt file, the resume, and the translation.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

from agent_core_lib.agent_core_lib.helpers.command_floor import GIT_ACCESS_LOCAL
from agent_core_lib.agent_core_lib.session.turn_events import (
    TURN_EVENT_TURN_COMPLETED,
    TURN_EVENT_TURN_FAILED,
)
from grok_core_lib.grok_core_lib.session.streaming import (
    SessionEvent,
    StreamingGrokSession,
)

_FAKE_GROK = '''\
#!{python}
import json, sys
args = sys.argv[1:]
def value(flag):
    return args[args.index(flag) + 1] if flag in args else ''
with open({log!r}, 'a', encoding='utf-8') as log:
    log.write(json.dumps(args) + '\\n')
prompt = open(value('--prompt-file'), encoding='utf-8').read()
session = value('--resume') or 'sess-new'
if 'FAIL' in prompt:
    print(json.dumps({{"type": "result", "subtype": "error_during_execution",
                       "is_error": True, "errors": ["Not signed in"], "session_id": session}}))
    sys.exit(1)
print(json.dumps({{"type": "system", "subtype": "init", "session_id": session}}))
print(json.dumps({{"type": "assistant", "message": {{"content": [
    {{"type": "text", "text": "got " + prompt.strip()[-12:]}}]}}, "session_id": session}}))
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False,
                   "result": "ok", "session_id": session, "usage": {{}}}}))
'''


def _wait(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class _Harness(unittest.TestCase):
    def setUp(self) -> None:
        folder = Path(tempfile.mkdtemp())
        self.log = folder / 'argv.jsonl'
        self.binary = folder / 'grok'
        self.binary.write_text(
            _FAKE_GROK.format(python=sys.executable, log=str(self.log)), encoding='utf-8',
        )
        self.binary.chmod(self.binary.stat().st_mode | stat.S_IXUSR)
        self.cwd = folder / 'task' / 'repo-a'
        self.cwd.mkdir(parents=True)

    def _session(self, **kwargs) -> StreamingGrokSession:
        session = StreamingGrokSession(
            task_id='T-1', cwd=str(self.cwd), binary=str(self.binary), **kwargs,
        )
        self.addCleanup(session.terminate)
        return session

    def _calls(self) -> list[list[str]]:
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def _settled(self, session, turns: int) -> bool:
        return _wait(lambda: self.log.exists() and len(self._calls()) == turns
                     and not session.is_working and session.terminal_event is not None)


@unittest.skipIf(sys.platform.startswith('win'), 'the fake CLI is a shebang script')
class GrokChatTests(_Harness):
    def test_a_turn_streams_a_reply_and_learns_the_session(self) -> None:
        session = self._session(model='grok-4.6')
        session.send_user_message('hello grok')
        self.assertTrue(self._settled(session, 1))
        self.assertEqual(session.agent_session_id, 'sess-new')
        self.assertEqual(session.terminal_event.event_type, TURN_EVENT_TURN_COMPLETED)
        replies = [e.raw['item']['text'] for e in session.recent_events()
                   if e.event_type == 'item.completed']
        self.assertEqual(replies, ['got hello grok'])
        argv = self._calls()[0]
        self.assertIn('--prompt-file', argv)
        self.assertEqual(argv[argv.index('--cwd') + 1], str(self.cwd))
        self.assertEqual(argv[argv.index('-m') + 1], 'grok-4.6')
        self.assertIn('Bash(git commit*)', argv)
        self.assertNotIn('--resume', argv)

    def test_the_next_turn_resumes_and_keeps_its_working_directory(self) -> None:
        session = self._session()
        session.send_user_message('one')
        self.assertTrue(self._settled(session, 1))
        session.send_user_message('two')
        self.assertTrue(self._settled(session, 2))
        second = self._calls()[1]
        self.assertEqual(second[second.index('--resume') + 1], 'sess-new')
        self.assertNotIn('--cwd', second)

    def test_local_git_access_releases_commit_but_not_push(self) -> None:
        session = self._session(git_access_source=lambda: GIT_ACCESS_LOCAL)
        session.send_user_message('commit it')
        self.assertTrue(self._settled(session, 1))
        argv = self._calls()[0]
        self.assertNotIn('Bash(git commit*)', argv)
        self.assertIn('Bash(git push*)', argv)

    def test_several_repositories_start_in_the_folder_holding_them(self) -> None:
        sibling = self.cwd.parent / 'repo-b'
        sibling.mkdir()
        session = self._session(additional_dirs=(str(sibling),))
        session.send_user_message('across repos')
        self.assertTrue(self._settled(session, 1))
        argv = self._calls()[0]
        self.assertEqual(argv[argv.index('--cwd') + 1], str(self.cwd.parent))

    def test_a_refused_turn_ends_failed_with_grok_s_reason(self) -> None:
        session = self._session()
        session.send_user_message('FAIL please')
        self.assertTrue(self._settled(session, 1))
        self.assertEqual(session.terminal_event.event_type, TURN_EVENT_TURN_FAILED)
        self.assertEqual(session.terminal_event.raw['error']['message'], 'Not signed in')

    def test_terminate_removes_the_prompt_file(self) -> None:
        session = self._session()
        session.send_user_message('one')
        self.assertTrue(self._settled(session, 1))
        folder = session._prompt_dir
        self.assertTrue(os.path.isdir(folder))
        session.terminate()
        self.assertFalse(os.path.exists(folder))
        session.terminate()


class SessionShapeTests(unittest.TestCase):
    def test_it_is_a_per_turn_chat_with_the_prompt_in_a_file(self) -> None:
        self.assertFalse(StreamingGrokSession.PROMPT_ON_STDIN)
        self.assertEqual(StreamingGrokSession.DEFAULT_BINARY, 'grok')
        self.assertIs(SessionEvent.__name__, 'TurnEvent')

    def test_the_default_command_writes_the_prompt_it_names(self) -> None:
        session = StreamingGrokSession(task_id='T', cwd='/work/repo')
        self.addCleanup(session.terminate)
        argv = session._default_command(prompt='the prompt', resume_id='abc')
        path = argv[argv.index('--prompt-file') + 1]
        self.assertEqual(Path(path).read_text(encoding='utf-8'), 'the prompt')
        self.assertEqual(argv[argv.index('--resume') + 1], 'abc')
        self.assertEqual(argv[0], 'grok')


if __name__ == '__main__':  # pragma: no cover
    unittest.main()

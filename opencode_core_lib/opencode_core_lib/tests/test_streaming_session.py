"""An OpenCode chat against a fake ``opencode`` executable that parses the real argv.

The fake answers ``--version`` (as V2, so runs are ``--standalone``), reads the
prompt from stdin, records the environment it was given, and prints
``--format json`` lines with NO done event — exactly the shape that makes a
clean exit the end of an OpenCode turn.
"""

from __future__ import annotations

import json
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
from opencode_core_lib.opencode_core_lib.helpers.cli_generation import (
    reset_cli_generation_cache,
)
from opencode_core_lib.opencode_core_lib.session.streaming import (
    SessionEvent,
    StreamingOpenCodeSession,
)

_FAKE_OPENCODE = '''\
#!{python}
import json, os, sys
args = sys.argv[1:]
if args == ['--version']:
    print('2.2.7')
    sys.exit(0)
def value(flag):
    return args[args.index(flag) + 1] if flag in args else ''
prompt = sys.stdin.read()
with open({log!r}, 'a', encoding='utf-8') as log:
    log.write(json.dumps({{"argv": args, "pwd": os.environ.get("PWD", ""),
                           "config": os.environ.get("OPENCODE_CONFIG_CONTENT", "")}}) + '\\n')
session = value('--session') or 'ses_new'
print(json.dumps({{"type": "step_start", "sessionID": session, "part": {{}}}}))
if 'FAIL' in prompt:
    print(json.dumps({{"type": "error", "sessionID": session,
                       "error": {{"type": "auth", "message": "No provider key"}}}}))
    sys.exit(1)
print(json.dumps({{"type": "text", "sessionID": session,
                   "part": {{"type": "text", "text": "got " + prompt.strip()[-12:]}}}}))
print(json.dumps({{"type": "step_finish", "sessionID": session, "part": {{"tokens": {{}}}}}}))
'''


def _wait(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@unittest.skipIf(sys.platform.startswith('win'), 'the fake CLI is a shebang script')
class OpenCodeChatTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_cli_generation_cache()
        self.addCleanup(reset_cli_generation_cache)
        folder = Path(tempfile.mkdtemp())
        self.log = folder / 'runs.jsonl'
        self.binary = folder / 'opencode'
        self.binary.write_text(
            _FAKE_OPENCODE.format(python=sys.executable, log=str(self.log)), encoding='utf-8',
        )
        self.binary.chmod(self.binary.stat().st_mode | stat.S_IXUSR)
        self.cwd = folder / 'task' / 'repo-a'
        self.cwd.mkdir(parents=True)

    def _session(self, **kwargs) -> StreamingOpenCodeSession:
        session = StreamingOpenCodeSession(
            task_id='T-1', cwd=str(self.cwd), binary=str(self.binary), **kwargs,
        )
        self.addCleanup(session.terminate)
        return session

    def _runs(self) -> list[dict]:
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def _settled(self, session, turns: int) -> bool:
        return _wait(lambda: self.log.exists() and len(self._runs()) == turns
                     and not session.is_working and session.terminal_event is not None)

    def test_a_clean_exit_completes_the_turn(self) -> None:
        session = self._session(model='anthropic/claude-sonnet-4-5')
        session.send_user_message('hello opencode')
        self.assertTrue(self._settled(session, 1))
        self.assertEqual(session.terminal_event.event_type, TURN_EVENT_TURN_COMPLETED)
        self.assertEqual(session.agent_session_id, 'ses_new')
        replies = [e.raw['item']['text'] for e in session.recent_events()
                   if e.event_type == 'item.completed']
        self.assertEqual(replies, ['got llo opencode'])
        run = self._runs()[0]
        self.assertIn('--standalone', run['argv'])
        self.assertEqual(run['argv'][run['argv'].index('-m') + 1], 'anthropic/claude-sonnet-4-5')
        self.assertEqual(run['pwd'], str(self.cwd))
        shell = json.loads(run['config'])['permission']['bash']
        self.assertEqual(shell['git commit *'], 'deny')

    def test_the_next_turn_resumes_the_session(self) -> None:
        session = self._session()
        session.send_user_message('one')
        self.assertTrue(self._settled(session, 1))
        session.send_user_message('two')
        self.assertTrue(self._settled(session, 2))
        second = self._runs()[1]['argv']
        self.assertEqual(second[second.index('--session') + 1], 'ses_new')

    def test_an_error_line_fails_the_turn_with_opencode_s_words(self) -> None:
        session = self._session()
        session.send_user_message('FAIL please')
        self.assertTrue(self._settled(session, 1))
        self.assertEqual(session.terminal_event.event_type, TURN_EVENT_TURN_FAILED)
        self.assertEqual(session.terminal_event.raw['error']['message'], 'No provider key')

    def test_local_git_access_and_several_repositories(self) -> None:
        sibling = self.cwd.parent / 'repo-b'
        sibling.mkdir()
        session = self._session(
            git_access_source=lambda: GIT_ACCESS_LOCAL, additional_dirs=(str(sibling),),
        )
        session.send_user_message('across repos')
        self.assertTrue(self._settled(session, 1))
        run = self._runs()[0]
        self.assertEqual(run['pwd'], str(self.cwd.parent))
        shell = json.loads(run['config'])['permission']['bash']
        self.assertNotIn('git commit *', shell)
        self.assertEqual(shell['git push *'], 'deny')


class SessionShapeTests(unittest.TestCase):
    def test_it_is_a_per_turn_chat_completed_by_a_clean_exit(self) -> None:
        self.assertTrue(StreamingOpenCodeSession.PROMPT_ON_STDIN)
        self.assertTrue(StreamingOpenCodeSession.CLEAN_EXIT_ENDS_TURN)
        self.assertEqual(StreamingOpenCodeSession.DEFAULT_BINARY, 'opencode')
        self.assertIs(SessionEvent.__name__, 'TurnEvent')


if __name__ == '__main__':  # pragma: no cover
    unittest.main()

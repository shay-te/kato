"""Strict or local git for a Codex chat.

Codex has no tool-deny flag and no system prompt. Its own ``workspace-write``
sandbox is the wall: it keeps every root's ``.git`` read-only, so in strict mode
even ``git add`` fails ("Unable to create .git/index.lock"). Local access hands
exactly those ``.git`` directories back, on EVERY turn and through ``-c`` —
proven on codex-cli 0.166: a resumed turn rejects ``--add-dir`` and does not
keep the first turn's, while the config override applies to both.

The level is asked at the start of each turn (a turn is its own process), so a
changed setting applies to the next message. The agent is told the rules in
front of the message only when the level changed, and the transcript keeps
what the operator typed.

Turns are driven by a real subprocess standing in for ``codex``; the command
the real CLI would get is checked through the session's own builder.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from codex_core_lib.codex_core_lib.session.manager import CodexSessionManager
from codex_core_lib.codex_core_lib.session.streaming import StreamingCodexSession

_LOCAL_MARK = 'GIT ACCESS FOR THIS SESSION: LOCAL'
_STRICT_AGAIN_MARK = 'GIT ACCESS FOR THIS SESSION: STRICT again'

# Stands in for ``codex exec``: saves what arrived on stdin, ends the turn.
_RECORDING_CLI = '''
    import json, sys
    with open(sys.argv[1], 'a', encoding='utf-8') as handle:
        handle.write(json.dumps(sys.stdin.read()) + "\\n")
    print(json.dumps({"type": "thread.started", "thread_id": "thread-1"}))
    print(json.dumps({"type": "turn.completed", "usage": {}}))
'''


def _repo(root: Path, name: str) -> Path:
    path = root / name
    path.mkdir()
    subprocess.run(['git', 'init', '-q', str(path)], check=True)
    return path


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class _Repos(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.api = _repo(self.root, 'api')
        self.web = _repo(self.root, 'web')
        self.notes = self.root / 'notes'  # a folder that is not a repository
        self.notes.mkdir()
        self.level = 'strict'

    def _session(self, **kwargs) -> StreamingCodexSession:
        return StreamingCodexSession(
            task_id='PROJ-1', cwd=str(self.api),
            additional_dirs=(str(self.web), str(self.notes)),
            git_access_source=lambda: self.level, **kwargs,
        )

    @staticmethod
    def _writable_roots(command: list[str]) -> list[str]:
        for flag, value in zip(command, command[1:]):
            if flag == '-c' and value.startswith('sandbox_workspace_write.writable_roots='):
                return json.loads(value.split('=', 1)[1])
        return []


class CommandTests(_Repos):
    def _command(self, session: StreamingCodexSession, *, resume_id: str = '') -> list[str]:
        session._turn_git_access = session._current_git_access()
        return session._default_command(prompt='x', resume_id=resume_id)

    def test_strict_leaves_the_sandbox_as_it_is(self) -> None:
        command = self._command(self._session())
        self.assertEqual(self._writable_roots(command), [])
        self.assertNotIn('-c', command)

    def test_local_opens_each_repositorys_git_and_nothing_else(self) -> None:
        self.level = 'local'
        command = self._command(self._session())
        self.assertEqual(
            self._writable_roots(command),
            [str(self.api / '.git'), str(self.web / '.git')],
        )
        self.assertIn('workspace-write', command)

    def test_a_resumed_turn_gets_it_too(self) -> None:
        # The case that broke ``--add-dir``: resumes reject it and do not
        # inherit it. The config override is accepted on ``exec resume``.
        self.level = 'local'
        command = self._command(self._session(), resume_id='thread-9')
        self.assertEqual(command[:4], ['codex', 'exec', 'resume', 'thread-9'])
        self.assertNotIn('--add-dir', command)
        self.assertEqual(len(self._writable_roots(command)), 2)

    def test_no_source_or_a_failing_one_is_strict(self) -> None:
        plain = StreamingCodexSession(task_id='PROJ-1', cwd=str(self.api))
        self.assertEqual(plain.git_access, 'strict')
        self.assertEqual(self._writable_roots(self._command(plain)), [])

        def broken() -> str:
            raise RuntimeError('settings unreadable')

        failing = StreamingCodexSession(
            task_id='PROJ-1', cwd=str(self.api), git_access_source=broken,
        )
        self.assertEqual(failing.git_access, 'strict')

    def test_an_unknown_level_is_strict(self) -> None:
        self.level = 'full'
        self.assertEqual(self._writable_roots(self._command(self._session())), [])


class TurnTests(_Repos):
    """Through real turns: what the agent receives, and what the chat shows."""

    def setUp(self) -> None:
        super().setUp()
        self.stdin_log = self.root / 'stdin.jsonl'
        script = self.root / 'fake_codex.py'
        script.write_text(textwrap.dedent(_RECORDING_CLI), encoding='utf-8')
        self.commands: list[list[str]] = []

        def build_command(*, prompt, resume_id):
            del prompt, resume_id
            return [sys.executable, str(script), str(self.stdin_log)]

        self.session = self._session(build_command=build_command)
        self.addCleanup(self.session.terminate)

    def _turn(self, text: str) -> str:
        before = len(self._received())
        self.session.send_user_message(text)
        self.assertTrue(_wait_until(
            lambda: len(self._received()) > before and not self.session.is_working,
        ))
        return self._received()[-1]

    def _received(self) -> list[str]:
        if not self.stdin_log.exists():
            return []
        return [json.loads(line) for line in self.stdin_log.read_text(encoding='utf-8').splitlines()]

    def _shown(self) -> list[str]:
        return [
            event.raw['message']['content'][0]['text']
            for event in self.session.recent_events()
            if event.raw.get('type') == 'user'
        ]

    def test_strict_sends_the_message_untouched(self) -> None:
        self.assertEqual(self._turn('fix the parser'), 'fix the parser')

    def test_local_is_told_once_then_not_again(self) -> None:
        self.level = 'local'
        first = self._turn('commit this')
        second = self._turn('now squash them')

        self.assertTrue(first.startswith(_LOCAL_MARK))
        self.assertTrue(first.endswith('commit this'))
        self.assertEqual(second, 'now squash them')

    def test_switching_mid_chat_applies_to_the_next_turn_and_says_so(self) -> None:
        self.assertEqual(self._turn('one'), 'one')
        self.level = 'local'
        self.assertTrue(self._turn('two').startswith(_LOCAL_MARK))
        self.level = 'strict'
        back = self._turn('three')
        self.assertTrue(back.startswith(_STRICT_AGAIN_MARK))
        self.assertTrue(back.endswith('three'))

    def test_the_chat_shows_what_the_operator_typed(self) -> None:
        self.level = 'local'
        self._turn('commit this')
        self.assertEqual(self._shown(), ['commit this'])

    def test_the_level_reported_is_the_running_turns(self) -> None:
        self.level = 'local'
        self._turn('commit this')
        self.assertEqual(self.session.git_access, 'local')
        self.level = 'strict'
        # Between turns: what the NEXT turn would get.
        self.assertEqual(self.session.git_access, 'strict')


class ManagerTests(unittest.TestCase):
    def _spawned(self, **manager_kwargs) -> dict:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        made: list[dict] = []

        class _Fake:
            def __init__(self, **kwargs) -> None:
                made.append(kwargs)
                self.is_alive = True
                self._session_id_correction_callback = None

            def start(self, initial_prompt: str = '') -> None:
                pass

        manager = CodexSessionManager(
            state_dir=tmp.name, session_factory=_Fake, **manager_kwargs,
        )
        manager.start_session(task_id='PROJ-1', cwd=tmp.name)
        return made[-1]

    def test_the_manager_hands_its_source_to_every_chat(self) -> None:
        source = lambda: 'local'  # noqa: E731
        self.assertIs(self._spawned(git_access_source=source)['git_access_source'], source)

    def test_no_source_passes_nothing(self) -> None:
        self.assertNotIn('git_access_source', self._spawned())


if __name__ == '__main__':
    unittest.main()

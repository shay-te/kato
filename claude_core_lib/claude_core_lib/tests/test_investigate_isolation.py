"""``investigate``: a fresh, read-only, stoppable turn that leaves the client alone.

Driven against a real executable standing in for the CLI (a small Python script
that records its argv and stdin), so what is asserted is the command a real run
receives and how a real process dies — not a mocked ``subprocess.run``.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from agent_core_lib.agent_core_lib.helpers.cancellable_process import ProcessCancelled
from agent_core_lib.agent_core_lib.helpers.read_only_tools import (
    READ_ONLY_ALLOWED_TOOLS,
)
from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient

_FAKE_CLI = '''#!{python}
import json, sys, time
data = sys.stdin.read()
with open({record!r}, "w") as handle:
    json.dump({{"argv": sys.argv[1:], "stdin": data}}, handle)
time.sleep({sleep})
print(json.dumps({{"type": "result", "result": "reviewed: " + data[:12], "is_error": False}}))
'''


def _fake_cli(test: unittest.TestCase, *, sleep: float = 0.0) -> tuple[str, str]:
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    record = os.path.join(tmp.name, 'call.json')
    path = os.path.join(tmp.name, 'fake-cli')
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(_FAKE_CLI.format(python=sys.executable, record=record, sleep=sleep))
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path, record


def _recorded(record: str) -> dict:
    with open(record, encoding='utf-8') as handle:
        return json.load(handle)


def _flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


@unittest.skipIf(os.name == 'nt', 'shebang-script stand-in for the CLI is POSIX')
class FreshReadOnlyTurnTests(unittest.TestCase):

    def test_a_review_run_is_read_only_fresh_and_sees_every_repo(self) -> None:
        binary, record = _fake_cli(self)
        client = ClaudeCliClient(
            binary=binary, allowed_tools='Bash,Edit', disallowed_tools='WebSearch',
        )
        with tempfile.TemporaryDirectory() as task:
            repo = os.path.join(task, 'api')
            os.makedirs(repo)
            text = client.investigate(
                'review this', cwd=repo, additional_dirs=[task],
                task_id='PROJ-1', log_label='review',
            )
            argv = _recorded(record)['argv']
        self.assertEqual(text, 'reviewed: review this')
        self.assertEqual(_flag(argv, '--allowedTools'), READ_ONLY_ALLOWED_TOOLS)
        denied = _flag(argv, '--disallowedTools').split(',')
        for tool in ('Edit', 'Write', 'Bash'):
            self.assertIn(tool, denied)
        self.assertIn('--no-session-persistence', argv)
        self.assertNotIn('--resume', argv)
        self.assertEqual(_flag(argv, '--add-dir'), task)

    def test_the_client_keeps_its_own_tools_while_a_run_is_in_flight(self) -> None:
        # The client is shared. Writing the read-only split onto it for the
        # length of a run let anything spawning meanwhile pick it up.
        binary, _record = _fake_cli(self, sleep=0.5)
        client = ClaudeCliClient(
            binary=binary, allowed_tools='Bash,Edit', disallowed_tools='WebSearch',
        )
        seen: list[tuple[str, str]] = []
        runner = threading.Thread(
            target=client.investigate, args=('review',), kwargs={'cwd': tempfile.gettempdir()},
        )
        runner.start()
        time.sleep(0.2)
        seen.append((client._allowed_tools, client._disallowed_tools))
        runner.join(10)
        seen.append((client._allowed_tools, client._disallowed_tools))
        self.assertEqual(seen, [('Bash,Edit', 'WebSearch')] * 2)

    def test_a_triage_call_keeps_its_old_shape(self) -> None:
        binary, record = _fake_cli(self)
        client = ClaudeCliClient(binary=binary)
        client.investigate('classify', cwd=tempfile.gettempdir())
        argv = _recorded(record)['argv']
        self.assertNotIn('--add-dir', argv)
        self.assertEqual(_flag(argv, '--allowedTools'), READ_ONLY_ALLOWED_TOOLS)


@unittest.skipIf(os.name == 'nt', 'shebang-script stand-in for the CLI is POSIX')
class StoppableTurnTests(unittest.TestCase):

    def test_cancel_kills_the_cli_and_raises(self) -> None:
        binary, _record = _fake_cli(self, sleep=30)
        client = ClaudeCliClient(binary=binary)
        cancel = threading.Event()
        threading.Timer(0.4, cancel.set).start()
        started = time.monotonic()
        with self.assertRaises(ProcessCancelled):
            client.investigate('review', cwd=tempfile.gettempdir(), cancel_event=cancel)
        self.assertLess(time.monotonic() - started, 10)


class DockerMountTests(unittest.TestCase):

    def test_a_task_root_mounts_the_whole_task_and_keeps_the_repo_as_workdir(self) -> None:
        client = ClaudeCliClient(binary='claude', docker_mode_on=True)
        task = os.path.normpath('/w/PROJ-1')
        repo = os.path.join(task, 'api')
        with patch(
            'claude_core_lib.claude_core_lib.cli_client.wrap_spawn_for_docker',
            return_value=(['docker', 'run'], 'box-1'),
        ) as wrap, patch(
            'claude_core_lib.claude_core_lib.cli_client.subprocess.run',
            return_value=_completed('{"result": "ok"}'),
        ):
            client.investigate('review', cwd=repo, sandbox_root=task, task_id='PROJ-1')
        kwargs = wrap.call_args.kwargs
        self.assertEqual(kwargs['workspace_path'], task)
        self.assertEqual(kwargs['workdir_subpath'], 'api')
        self.assertEqual(kwargs['task_id'], 'PROJ-1')

    def test_a_cancelled_docker_run_stops_its_container(self) -> None:
        client = ClaudeCliClient(binary='claude', docker_mode_on=True)
        with patch(
            'claude_core_lib.claude_core_lib.cli_client.wrap_spawn_for_docker',
            return_value=(['docker', 'run'], 'box-2'),
        ), patch(
            'claude_core_lib.claude_core_lib.cli_client.run_cancellable',
            side_effect=ProcessCancelled('stopped'),
        ), patch(
            'sandbox_core_lib.sandbox_core_lib.manager.kill_container',
        ) as kill:
            with self.assertRaises(ProcessCancelled):
                client.investigate(
                    'review', cwd='/w/PROJ-1/api', cancel_event=threading.Event(),
                )
        kill.assert_called_once()
        self.assertEqual(kill.call_args.args[0], 'box-2')


def _completed(stdout: str):
    import subprocess
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr='')


if __name__ == '__main__':
    unittest.main()

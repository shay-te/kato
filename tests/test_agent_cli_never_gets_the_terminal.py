"""No agent CLI kato starts is handed the operator's terminal as its stdin.

Measured: ``claude --help`` with a TTY stdin switches the terminal to raw mode
while it runs (Node / libuv raw: ``-isig -icanon -echo``, ``opost`` kept), and
a probe killed by its timeout mid-run LEAVES it raw. With ``-isig`` the
terminal sends no SIGINT for Ctrl+C, so kato could not be stopped from the
terminal it runs in — and after it exited the shell had no echo either.

Driven for real: the probes run inside a pseudo-terminal (the operator's
terminal) through kato's own functions, against a stand-in CLI that records
whether its stdin is a TTY. Every launch must say no.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

_FAKE_CLI = textwrap.dedent('''\
    #!{python}
    import json, os, sys
    with open({record!r}, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({{"argv": sys.argv[1:], "tty": os.isatty(0)}}) + "\\n")
    args = sys.argv[1:]
    if "--version" in args:
        print({version!r})
    elif "--help" in args:
        print('--effort <level>  (choices: "low", "medium", "high")\\n--json\\nremote-control')
''')

# Runs INSIDE the pseudo-terminal: every probe kato makes, the way it makes it.
_PROBES = textwrap.dedent('''\
    import sys
    sys.path.insert(0, {repo!r})
    from claude_core_lib.claude_core_lib.helpers import effort_levels, remote_control
    from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
    from codex_core_lib.codex_core_lib.cli_client import CodexCliClient
    from kato_core_lib.helpers import agent_version_utils
    claude, codex = {claude!r}, {codex!r}
    def attempt(run):
        try:
            run()
        except Exception:
            pass  # only the launch matters here, not what the probe concluded
    attempt(lambda: effort_levels._parse_effort_levels_from_help(claude, 10))
    attempt(lambda: remote_control._probe_help_for_remote_control(claude, 10))
    attempt(lambda: ClaudeCliClient(binary=claude).validate_connection())
    attempt(lambda: agent_version_utils._default_runner(claude))
    attempt(lambda: agent_version_utils._default_upgrade_runner([claude, 'update']))
    attempt(lambda: CodexCliClient(binary=codex).validate_connection())
    print('PROBES-DONE', flush=True)
''')


@unittest.skipIf(os.name == 'nt', 'pseudo-terminals are POSIX')
class AgentCliNeverGetsTheTerminalTests(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.record = self.root / 'launches.jsonl'

    def _fake(self, name: str, version: str) -> str:
        path = self.root / name
        path.write_text(_FAKE_CLI.format(
            python=sys.executable, record=str(self.record), version=version,
        ), encoding='utf-8')
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return str(path)

    def test_every_probe_runs_without_the_operators_terminal(self) -> None:
        import pty
        claude = self._fake('claude', '2.1.0 (Claude Code)')
        codex = self._fake('codex', 'codex-cli 1.2.3')
        script = self.root / 'probes.py'
        script.write_text(_PROBES.format(repo=str(REPO), claude=claude, codex=codex),
                          encoding='utf-8')
        pid, fd = pty.fork()
        if pid == 0:  # pragma: no cover - the child process
            os.execv(sys.executable, [sys.executable, str(script)])
        output = b''
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            output += chunk
        os.waitpid(pid, 0)
        os.close(fd)
        self.assertIn(b'PROBES-DONE', output, output.decode(errors='replace')[-2000:])

        launches = [json.loads(line) for line in self.record.read_text().splitlines()]
        seen = {(Path(sys.executable).name, ' '.join(entry['argv'])) for entry in launches}
        # Every kind of probe actually ran — an empty record would pass vacuously.
        for expected in ('--help', '--version', 'update', 'exec --help'):
            self.assertTrue(any(argv == expected for _, argv in seen), (expected, seen))
        handed_the_terminal = [entry['argv'] for entry in launches if entry['tty']]
        self.assertEqual(handed_the_terminal, [])


if __name__ == '__main__':
    unittest.main()

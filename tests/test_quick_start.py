"""Quick start: the first run finds what it can and asks only the rest.

Real folders on disk for the code-folder search; a real child process standing
in for ``claude auth login`` (it prints the link, asks for a code on a line with
no newline, and checks what it is given); real settings writes into a temporary
file. Only ``git`` and ``xcode-select`` are stand-ins: what they answer depends
on the computer the tests run on.
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
from unittest import mock

from kato_core_lib.data_layers.service.quick_start import agent_check, code_folders, git_check
from kato_core_lib.data_layers.service.quick_start.claude_sign_in import ClaudeSignIn, SignInError
from kato_core_lib.data_layers.service.quick_start.settings import QuickStartError, save_quick_start


class _Home(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve()

    def repos(self, folder: str, *names: str) -> None:
        for name in names:
            (self.home / folder / name / '.git').mkdir(parents=True)


class CodeFoldersTests(_Home):
    def found(self, **options) -> list[tuple[str, int]]:
        return [(str(Path(f.path).relative_to(self.home)), f.repositories)
                for f in code_folders.find_code_folders(self.home, **options)]

    def test_the_folder_with_the_most_repositories_directly_in_it_comes_first(self) -> None:
        self.repos('dev', 'api', 'web', 'cli')
        self.repos('Desktop/dev', 'one', 'two')
        self.repos('Desktop/old/copies', 'api', 'web', 'cli', 'docs')  # nested only
        found = self.found()
        self.assertEqual(found[0], ('dev', 3))
        self.assertIn(('Desktop/dev', 2), found)

    def test_a_folder_gives_way_to_the_subfolder_holding_all_its_repositories(self) -> None:
        self.repos('Desktop/dev', 'api', 'web')
        self.assertEqual(self.found(), [('Desktop/dev', 2)])  # never ~/Desktop as well

    def test_an_organisation_layout_counts_one_level_down(self) -> None:
        # Repositories directly in a folder rank first (that is what keeps
        # ~/Desktop/dev ahead of ~/Desktop), so the organisation folder leads;
        # the folder holding every organisation is offered beside it.
        self.repos('work/acme', 'api', 'web')
        self.repos('work/other', 'tool')
        found = self.found()
        self.assertEqual(found[0], ('work/acme', 2))
        self.assertIn(('work', 3), found)

    def test_hidden_big_and_excluded_folders_and_repositories_themselves_are_not_offered(self) -> None:
        self.repos('.kato/workspaces/T-1', 'api')
        self.repos('Library/Caches', 'thing')
        self.repos('node_modules', 'pkg')
        self.repos('kato-workspaces', 'api')
        self.repos('', 'loose-repo')  # a repository directly in home is not a code folder
        (self.home / 'loose-repo' / 'sub' / '.git').mkdir(parents=True)
        self.assertEqual(self.found(exclude=[self.home / 'kato-workspaces']), [])

    def test_nothing_found_on_a_fresh_computer_and_the_folder_offered_instead(self) -> None:
        (self.home / 'Documents').mkdir()
        self.assertEqual(self.found(), [])
        self.assertEqual(code_folders.new_folder(self.home), self.home / 'code')

    def test_the_time_budget_stops_the_search_with_what_it_found(self) -> None:
        self.repos('a', 'r1')
        self.repos('b', 'r2')
        ticks = iter([0.0, 0.0, 0.0, 0.0, 0.0, 99.0])
        found = code_folders.find_code_folders(self.home, clock=lambda: next(ticks, 99.0), seconds=1.0)
        self.assertLessEqual(len(found), 1)

    def test_at_most_five_and_a_capped_read_per_folder(self) -> None:
        for index in range(7):
            self.repos(f'f{index}', *(f'r{n}' for n in range(index + 1)))
        found = self.found()
        self.assertEqual(len(found), code_folders.MAX_RESULTS)
        self.assertEqual(found[0], ('f6', 7))
        with mock.patch.object(code_folders, 'MAX_ENTRIES', 2):
            self.assertEqual(code_folders.find_code_folders(self.home)[0].repositories, 2)

    def test_an_unreadable_folder_is_skipped_not_fatal(self) -> None:
        self.repos('dev', 'api')
        with mock.patch.object(Path, 'iterdir', side_effect=PermissionError('no')):
            self.assertEqual(code_folders.find_code_folders(self.home), [])
        with mock.patch.object(Path, 'exists', side_effect=PermissionError('no')):
            self.assertEqual(code_folders.find_code_folders(self.home), [])

    def test_files_beside_the_folders_are_ignored(self) -> None:
        self.repos('dev', 'api')
        (self.home / 'notes.txt').write_text('x')
        (self.home / 'dev' / 'README.md').write_text('x')
        self.assertEqual(self.found(), [('dev', 1)])

    def test_the_budget_can_run_out_inside_a_folder_too(self) -> None:
        self.repos('dev', 'api', 'web')
        self.repos('dev/nested', 'tool')
        for spent_after in range(1, 8):
            with self.subTest(spent_after=spent_after):
                ticks = iter([0.0] * spent_after)
                found = code_folders.find_code_folders(self.home, clock=lambda: next(ticks, 99.0), seconds=1.0)
                self.assertLessEqual(len(found), 1)

    def test_a_folder_entry_is_ready_for_json(self) -> None:
        self.assertEqual(code_folders.CodeFolder('/x', 2).to_dict(), {'path': '/x', 'repositories': 2})


class GitCheckTests(unittest.TestCase):
    def run_answering(self, answers: dict):
        calls = []

        def run(argv, **kwargs):
            calls.append(argv)
            answer = answers[argv[0] if argv[0] != '/usr/bin/git' else 'git']
            if isinstance(answer, Exception):
                raise answer
            return subprocess.CompletedProcess(argv, answer[0], stdout=answer[1], stderr='')
        return run, calls

    def test_an_installed_git_reports_its_version(self) -> None:
        run, _ = self.run_answering({'git': (0, 'git version 2.43.0\n'), 'xcode-select': (0, '/x')})
        status = git_check.git_status(platform='linux', which=lambda name: '/usr/bin/git', run=run)
        self.assertEqual(status, {'installed': True, 'version': '2.43.0', 'install': None})

    def test_a_mac_without_the_command_line_tools_has_no_git_and_its_stand_in_never_runs(self) -> None:
        run, calls = self.run_answering({'xcode-select': (2, ''), 'git': (0, 'git version 1')})
        status = git_check.git_status(platform='darwin', which=lambda name: '/usr/bin/git', run=run)
        self.assertFalse(status['installed'])
        self.assertEqual(status['install']['command'], 'xcode-select --install')
        self.assertTrue(status['install']['can_start'])
        self.assertEqual(calls, [['xcode-select', '-p']])  # the stand-in would pop a dialog

    def test_a_mac_with_the_tools_asks_git(self) -> None:
        run, calls = self.run_answering({'xcode-select': (0, '/Library/Developer'), 'git': (0, 'git version 2.39.5 (Apple Git-154)')})
        status = git_check.git_status(platform='darwin', which=lambda name: '/usr/bin/git', run=run)
        self.assertEqual(status['version'], '2.39.5 (Apple Git-154)')
        self.assertEqual(calls, [['xcode-select', '-p'], ['/usr/bin/git', '--version']])

    def test_no_git_or_a_git_that_does_not_answer_is_missing_with_the_way_to_get_it(self) -> None:
        missing = git_check.git_status(platform='linux', which=lambda name: None, run=None)
        self.assertEqual(missing['install']['command'], 'sudo apt install git')
        broken, _ = self.run_answering({'git': (1, 'error')})
        self.assertFalse(git_check.git_status(platform='win32', which=lambda name: 'git', run=broken)['installed'])
        self.assertEqual(git_check.install_hint('win32')['command'], 'winget install --id Git.Git -e')
        failing, _ = self.run_answering({'git': OSError('gone'), 'xcode-select': subprocess.TimeoutExpired('x', 1)})
        self.assertFalse(git_check.git_status(platform='linux', which=lambda name: 'git', run=failing)['installed'])
        self.assertFalse(git_check.git_status(platform='darwin', which=lambda name: 'git', run=failing)['installed'])

    def test_the_mac_installer_opens_only_on_a_mac(self) -> None:
        spawn = mock.Mock()
        self.assertTrue(git_check.start_mac_install(platform='darwin', spawn=spawn))
        self.assertEqual(spawn.call_args.args[0], ['xcode-select', '--install'])
        self.assertFalse(git_check.start_mac_install(platform='linux', spawn=spawn))
        self.assertFalse(git_check.start_mac_install(platform='darwin', spawn=mock.Mock(side_effect=OSError('no'))))


class AgentCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        on_path = mock.patch.object(agent_check, 'add_agent_install_dirs_to_path')
        self.on_path = on_path.start()
        self.addCleanup(on_path.stop)

    def test_a_missing_cli_says_whether_kato_can_install_it_and_how_by_hand(self) -> None:
        plan = {'allowed': True, 'command': 'curl -fsSL https://claude.ai/install.sh | bash', 'reason': ''}
        status = agent_check.agent_status('claude', {}, version_of=lambda env, backend: None,
                                          plan_for=lambda env, backend: plan,
                                          readiness=lambda agent, binary: 'Claude CLI not found')
        self.assertEqual(status['installed'], False)
        self.assertEqual(status['install'], {'available': True, 'command': plan['command'], 'reason': ''})
        self.assertEqual(status['help'], 'Claude CLI not found')
        self.assertEqual(status['download'], 'https://code.claude.com/docs/en/setup')
        self.assertIsNone(status['logged_in'])
        self.on_path.assert_called_once_with()

    def test_an_installed_claude_reports_whether_it_is_signed_in(self) -> None:
        seen = {}

        def version_of(env, backend):
            seen['backend'] = backend
            return '2.1.325'
        status = agent_check.agent_status('claude', {'KATO_CLAUDE_BINARY': '/opt/claude'}, version_of=version_of,
                                          login_status=lambda binary: {'logged_in': False, 'binary': binary})
        self.assertEqual((status['installed'], status['version'], status['logged_in']), (True, '2.1.325', False))
        self.assertEqual((seen['backend'], status['binary']), ('claude', '/opt/claude'))
        self.assertEqual((status['install'], status['login_command']), (None, ''))

    def test_the_other_agents_name_their_own_sign_in_and_kato_cannot_tell(self) -> None:
        for agent, login in (('codex', 'codex login'), ('grok', 'grok login'), ('opencode', 'opencode auth login')):
            with self.subTest(agent=agent):
                status = agent_check.agent_status(agent, {}, version_of=lambda env, backend: '1.2.3')
                self.assertEqual((status['installed'], status['logged_in'], status['login_command']), (True, None, login))
                self.assertEqual(status['binary'], agent)
                self.assertTrue(status['download'].startswith('https://'))

    def test_a_missing_codex_without_npm_gets_its_own_install_words(self) -> None:
        refused = {'allowed': False, 'command': '', 'reason': 'no supported upgrade path for codex'}
        status = agent_check.agent_status('codex', {}, version_of=lambda env, backend: None,
                                          plan_for=lambda env, backend: refused,
                                          readiness=lambda agent, binary: 'Install Codex CLI: npm install -g @openai/codex')
        self.assertFalse(status['install']['available'])
        self.assertIn('npm install -g @openai/codex', status['help'])

    def test_every_agent_in_tab_order(self) -> None:
        statuses = agent_check.all_agents_status({}, version_of=lambda env, backend: '1.0.0',
                                                 login_status=lambda binary: {'logged_in': True})
        self.assertEqual(list(statuses), ['claude', 'codex', 'grok', 'opencode'])
        self.assertIs(statuses['claude']['logged_in'], True)

    def test_the_real_sign_in_check_is_the_default(self) -> None:
        with mock.patch('claude_core_lib.claude_core_lib.helpers.auth_status.claude_login_status',
                        return_value={'logged_in': True, 'method': 'claude.ai'}):
            status = agent_check.agent_status('claude', {}, version_of=lambda env, backend: '2.1.0')
        self.assertIs(status['logged_in'], True)


# A stand-in for ``claude auth login``: the link, then a prompt with no newline,
# then the code on its input decides how it ends.
_FAKE_LOGIN = textwrap.dedent('''
    import sys, time
    if '--hang' in sys.argv:
        time.sleep(60)
    sys.stdout.write("Opening browser to sign in...\\n")
    sys.stdout.write("If the browser didn't open, visit: https://claude.example/oauth?code=true&state=s1\\n")
    sys.stdout.write("Paste code here if prompted > ")
    sys.stdout.flush()
    code = sys.stdin.readline().strip()
    sys.exit(0 if code == 'abc#123' else 1)
''')


class ClaudeSignInTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.script = Path(tmp.name) / 'fake_login.py'
        self.script.write_text(_FAKE_LOGIN)
        self.argv: list = []

    def sign_in(self, *extra, **options) -> ClaudeSignIn:
        def spawn(argv, **kwargs):
            self.argv.append(argv)
            return subprocess.Popen([sys.executable, str(self.script), *extra], **kwargs)
        signing = ClaudeSignIn(spawn=spawn, **options)
        self.addCleanup(signing.cancel)
        return signing

    def wait_for(self, signing, predicate, timeout=10.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = signing.status()
            if predicate(status):
                return status
            time.sleep(0.02)
        raise AssertionError(f'never reached: {signing.status()}')

    def test_the_link_and_the_code_prompt_are_seen_and_the_pasted_code_signs_in(self) -> None:
        signing = self.sign_in()
        self.assertEqual(signing.start('/x/claude')['state'], 'waiting')
        self.assertEqual(self.argv, [['/x/claude', 'auth', 'login']])
        waiting = self.wait_for(signing, lambda status: status['asks_for_code'])
        self.assertEqual(waiting['link'], 'https://claude.example/oauth?code=true&state=s1')
        signing.submit_code('  abc#123 ')
        done = self.wait_for(signing, lambda status: status['state'] != 'waiting')
        self.assertEqual((done['state'], done['message'], done['asks_for_code']), ('done', 'signed in', False))

    def test_a_wrong_code_ends_the_sign_in_as_failed(self) -> None:
        signing = self.sign_in()
        signing.start('claude')
        self.wait_for(signing, lambda status: status['asks_for_code'])
        signing.submit_code('nope')
        failed = self.wait_for(signing, lambda status: status['state'] != 'waiting')
        self.assertEqual((failed['state'], failed['message']), ('failed', 'sign-in ended with exit code 1'))

    def test_a_code_kato_will_not_write_and_a_sign_in_not_waiting_are_refused(self) -> None:
        signing = self.sign_in()
        with self.assertRaises(SignInError):
            signing.submit_code('abc')  # nothing started
        signing.start('claude')
        self.wait_for(signing, lambda status: status['asks_for_code'])
        for bad in ('', 'two words', 'x' * 513, 'a\nb'):
            with self.subTest(code=bad), self.assertRaises(SignInError):
                signing.submit_code(bad)
        self.assertEqual(signing.start('claude')['state'], 'waiting')  # one at a time
        self.assertEqual(len(self.argv), 1)

    def test_a_sign_in_that_ends_before_the_code_arrives_says_so(self) -> None:
        signing = self.sign_in()
        signing.start('claude')
        self.wait_for(signing, lambda status: status['asks_for_code'])
        with signing._lock:
            signing._process.stdin.close()
        with self.assertRaises(SignInError) as caught:
            signing.submit_code('abc#123')
        self.assertIn('ended before the code arrived', str(caught.exception))

    def test_cancel_and_timeout_end_it(self) -> None:
        signing = self.sign_in('--hang')
        signing.start('claude')
        self.assertEqual(signing.cancel()['state'], 'cancelled')
        self.assertEqual(signing.cancel()['state'], 'cancelled')  # nothing left to cancel
        slow = self.sign_in('--hang', timeout_seconds=0.3)
        slow.start('claude')
        failed = self.wait_for(slow, lambda status: status['state'] != 'waiting')
        self.assertEqual(failed['message'], 'the sign-in took too long — start it again')

    def test_a_long_output_is_kept_only_up_to_its_cap(self) -> None:
        from kato_core_lib.data_layers.service.quick_start import claude_sign_in
        with mock.patch.object(claude_sign_in, 'MAX_OUTPUT_CHARS', 20):
            signing = self.sign_in()
            signing.start('claude')
            time.sleep(0.5)
            with signing._lock:
                self.assertEqual(len(signing._output), 20)

    def test_killing_or_closing_an_already_gone_cli_is_quiet(self) -> None:
        from kato_core_lib.data_layers.service.quick_start import claude_sign_in
        gone = mock.Mock()
        gone.kill.side_effect = ProcessLookupError('gone')
        claude_sign_in._kill(gone)
        broken = mock.Mock(stdin=None)
        broken.stdout.close.side_effect = OSError('closed')
        claude_sign_in._close_pipes(broken)
        broken.stdout.close.assert_called_once_with()

    def test_a_cli_that_cannot_start_is_a_failed_sign_in(self) -> None:
        signing = ClaudeSignIn(spawn=mock.Mock(side_effect=FileNotFoundError('no claude')))
        failed = signing.start('/missing/claude')
        self.assertEqual(failed['state'], 'failed')
        self.assertIn('could not start /missing/claude', failed['message'])
        self.assertEqual(ClaudeSignIn().status()['state'], 'idle')


class SaveQuickStartTests(_Home):
    def save(self, folder, *, agent='claude', create=False, env=None) -> dict:
        self.written: list[dict] = []
        return save_quick_start(folder, agent=agent, create=create, env=env or {},
                                write=self.written.append, home=self.home)

    def test_the_settings_are_written_together(self) -> None:
        (self.home / 'dev').mkdir()
        saved = self.save(str(self.home / 'dev'), agent='codex')
        self.assertEqual(saved, {
            'KATO_CHAT_AGENT': 'codex', 'KATO_AGENT_BACKEND': 'claude',
            'REPOSITORY_ROOT_PATH': str(self.home / 'dev'), 'KATO_ISSUE_PLATFORM': 'local',
        })
        self.assertEqual(self.written, [saved])

    def test_an_agent_kato_does_not_know_is_refused_before_anything_is_written(self) -> None:
        (self.home / 'dev').mkdir()
        for agent in ('', 'gpt-pilot'):
            with self.subTest(agent=agent), self.assertRaises(QuickStartError) as caught:
                self.save(str(self.home / 'dev'), agent=agent)
            self.assertIn('pick the agent you use', str(caught.exception))
            self.assertEqual(self.written, [])

    def test_a_ticket_system_already_set_is_kept(self) -> None:
        (self.home / 'dev').mkdir()
        saved = self.save(str(self.home / 'dev'), env={'KATO_ISSUE_PLATFORM': 'jira'})
        self.assertNotIn('KATO_ISSUE_PLATFORM', saved)

    def test_the_offered_folder_is_created_only_when_asked_and_only_inside_home(self) -> None:
        with self.assertRaises(QuickStartError) as caught:
            self.save(str(self.home / 'code'))
        self.assertIn('does not exist', str(caught.exception))
        saved = self.save(str(self.home / 'code'), create=True)
        self.assertTrue((self.home / 'code').is_dir())
        self.assertEqual(saved['REPOSITORY_ROOT_PATH'], str(self.home / 'code'))
        with tempfile.TemporaryDirectory() as elsewhere, self.assertRaises(QuickStartError) as outside:
            self.save(str(Path(elsewhere) / 'code'), create=True)
        self.assertIn('only creates a folder inside', str(outside.exception))

    def test_unusable_folders_are_refused_with_why(self) -> None:
        (self.home / 'notes.txt').write_text('x')
        for folder, reason in (('', 'pick the folder'), (str(self.home / 'notes.txt'), 'is a file')):
            with self.subTest(folder=folder), self.assertRaises(QuickStartError) as caught:
                self.save(folder)
            self.assertIn(reason, str(caught.exception))
        with mock.patch.object(Path, 'mkdir', side_effect=PermissionError('read-only')), \
                self.assertRaises(QuickStartError) as caught:
            self.save(str(self.home / 'code'), create=True)
        self.assertIn('could not create', str(caught.exception))
        with mock.patch.object(Path, 'resolve', side_effect=OSError('loop')), \
                self.assertRaises(QuickStartError) as caught:
            self.save(str(self.home / 'x'))
        self.assertIn('not a usable folder', str(caught.exception))

    def test_a_real_settings_file_gets_them(self) -> None:
        from kato_core_lib.helpers import kato_settings_store_utils as store
        (self.home / 'dev').mkdir()
        settings = self.home / '.kato' / 'settings.json'
        with mock.patch.object(store, 'kato_settings_path', return_value=settings):
            save_quick_start(str(self.home / 'dev'), agent='grok', create=False, env={},
                             write=store.write_kato_settings, home=self.home)
        written = json.loads(settings.read_text())
        self.assertEqual(written['REPOSITORY_ROOT_PATH'], str(self.home / 'dev'))
        self.assertEqual(written['KATO_AGENT_BACKEND'], 'claude')
        self.assertEqual(written['KATO_ISSUE_PLATFORM'], 'local')
        self.assertEqual(written['KATO_CHAT_AGENT'], 'grok')


if __name__ == '__main__':
    unittest.main()

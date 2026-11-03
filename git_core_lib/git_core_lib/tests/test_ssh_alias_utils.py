"""An SSH remote may name an alias from the operator's SSH config, not a host."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from git_core_lib.git_core_lib.helpers.ssh_alias_utils import (
    remote_with_real_host,
    ssh_host_name,
)

MODULE = 'git_core_lib.git_core_lib.helpers.ssh_alias_utils'


def ssh_prints(stdout: str, returncode: int = 0):
    return patch(
        f'{MODULE}.subprocess.run',
        return_value=SimpleNamespace(stdout=stdout, returncode=returncode),
    )


def use_ssh_config(test_case: unittest.TestCase, config: str) -> None:
    """The real ``ssh`` for this test, reading ``config``.

    Through a wrapper on PATH, because ssh reads the user's own config by
    default and ignores ``$HOME``.
    """
    ssh = shutil.which('ssh')
    if ssh is None or os.name == 'nt':
        test_case.skipTest('needs a POSIX ssh')
    # ssh refuses to run for a user id with no account, as in a bare container.
    probe = subprocess.run([ssh, '-G', 'localhost'], capture_output=True, stdin=subprocess.DEVNULL)
    if probe.returncode != 0:
        test_case.skipTest('ssh cannot run for this user')
    tmp = tempfile.TemporaryDirectory()
    test_case.addCleanup(tmp.cleanup)
    folder = Path(tmp.name)
    (folder / 'config').write_text(config, encoding='utf-8')
    wrapper = folder / 'ssh'
    wrapper.write_text(f'#!/bin/sh\nexec "{ssh}" -F "{folder}/config" "$@"\n', encoding='utf-8')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    path = patch.dict(os.environ, {'PATH': f'{folder}{os.pathsep}{os.environ["PATH"]}'})
    path.start()
    test_case.addCleanup(path.stop)


class RealSshTests(unittest.TestCase):
    def setUp(self) -> None:
        use_ssh_config(self, (
            'Host work\n  HostName bitbucket.org\n'
            'Host home-port-443\n  HostName ssh.github.com\n  Port 443\n'
            'Host company\n  HostName Git.Company.Example\n'
        ))

    def test_an_alias_becomes_the_host_it_stands_for(self) -> None:
        self.assertEqual(ssh_host_name('work'), 'bitbucket.org')

    def test_a_name_the_config_does_not_know_stays_as_it_is(self) -> None:
        self.assertEqual(ssh_host_name('git.example.org'), 'git.example.org')

    def test_an_scp_remote_gets_the_real_host_and_keeps_its_path(self) -> None:
        self.assertEqual(
            remote_with_real_host('git@work:team/api.git'),
            'ssh://bitbucket.org/team/api.git',
        )

    def test_an_ssh_url_gets_the_real_host_and_keeps_its_path(self) -> None:
        self.assertEqual(
            remote_with_real_host('ssh://git@work:2222/team/api.git'),
            'ssh://bitbucket.org/team/api.git',
        )

    def test_the_port_443_endpoint_is_the_service_it_stands_in_for(self) -> None:
        self.assertEqual(
            remote_with_real_host('git@home-port-443:team/api.git'),
            'ssh://github.com/team/api.git',
        )

    def test_a_host_comes_back_in_lower_case(self) -> None:
        self.assertEqual(ssh_host_name('company'), 'git.company.example')

    def test_a_real_host_is_left_exactly_as_written(self) -> None:
        self.assertEqual(
            remote_with_real_host('git@git.example.org:team/api.git'),
            'git@git.example.org:team/api.git',
        )


class NoLookupTests(unittest.TestCase):
    def assert_never_asks_ssh(self, remote: str) -> None:
        with patch(f'{MODULE}.subprocess.run') as run:
            self.assertEqual(remote_with_real_host(remote), remote)
        run.assert_not_called()

    def test_a_public_host_needs_no_lookup(self) -> None:
        self.assert_never_asks_ssh('git@github.com:team/api.git')
        self.assert_never_asks_ssh('git@bitbucket.org:team/api.git')
        self.assert_never_asks_ssh('ssh://git@gitlab.com/team/api.git')

    def test_an_address_that_is_not_ssh_needs_no_lookup(self) -> None:
        self.assert_never_asks_ssh('https://work/team/api.git')
        self.assert_never_asks_ssh('http://127.0.0.1:8080/team/api.git')

    def test_a_folder_is_not_a_host(self) -> None:
        self.assert_never_asks_ssh('/srv/git/api.git')
        self.assert_never_asks_ssh('C:/Users/me/api.git')
        self.assert_never_asks_ssh('C:\\Users\\me\\api.git')
        self.assert_never_asks_ssh('')

    def test_something_that_is_not_a_host_never_reaches_the_command_line(self) -> None:
        for name in ('-oProxyCommand=touch pwned', 'a b', 'a;b', '', 'UPPER'):
            with self.subTest(name=name), patch(f'{MODULE}.subprocess.run') as run:
                self.assertEqual(ssh_host_name(name), name)
                run.assert_not_called()


class SshCannotAnswerTests(unittest.TestCase):
    def test_no_ssh_on_this_machine(self) -> None:
        with patch(f'{MODULE}.shutil.which', return_value=None):
            self.assertEqual(ssh_host_name('work'), 'work')

    def test_a_config_ssh_cannot_read(self) -> None:
        with ssh_prints('hostname bitbucket.org\n', returncode=255):
            self.assertEqual(ssh_host_name('work'), 'work')

    def test_ssh_will_not_start(self) -> None:
        with patch(f'{MODULE}.subprocess.run', side_effect=OSError('no')):
            self.assertEqual(ssh_host_name('work'), 'work')

    def test_ssh_hangs(self) -> None:
        hung = subprocess.TimeoutExpired('ssh', 5)
        with patch(f'{MODULE}.subprocess.run', side_effect=hung):
            self.assertEqual(ssh_host_name('work'), 'work')

    def test_an_answer_without_a_host(self) -> None:
        with ssh_prints('user me\nhostname \nport 22\n'):
            self.assertEqual(ssh_host_name('work'), 'work')

    def test_the_remote_stays_usable_when_ssh_cannot_answer(self) -> None:
        with patch(f'{MODULE}.shutil.which', return_value=None):
            self.assertEqual(remote_with_real_host('git@work:team/api.git'), 'git@work:team/api.git')


class HowSshIsCalledTests(unittest.TestCase):
    def test_an_answer_in_mixed_case_is_lowered_like_every_other_host(self) -> None:
        # Today's ssh lowers it first. Remote parsing lowers hosts, and the
        # comparisons after this depend on the two agreeing.
        with ssh_prints('hostname Git.Company.Example\n'):
            self.assertEqual(ssh_host_name('work'), 'git.company.example')

    def test_it_never_gets_a_terminal_or_opens_a_window(self) -> None:
        with ssh_prints('hostname bitbucket.org\n') as run:
            ssh_host_name('work')
        self.assertEqual(run.call_args.args[0][1:], ['-G', 'work'])
        self.assertEqual(run.call_args.kwargs['stdin'], subprocess.DEVNULL)
        self.assertEqual(
            run.call_args.kwargs['creationflags'],
            getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        self.assertGreater(run.call_args.kwargs['timeout'], 0)


if __name__ == '__main__':
    unittest.main()

"""The computer's own folder dialog, opened by kato for its page.

Each kind of computer is driven through a stand-in for its dialog program
(``run``): the arguments it is given, a pick, a cancel. The dialog itself is
the operating system's; what is checked here is what kato asks of it and what
it makes of the answer.
"""
from __future__ import annotations

import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from flask import Flask

from kato_core_lib.helpers import native_folder_dialog as nfd
from kato_core_lib.helpers.native_folder_dialog import FolderDialogBusy, FolderDialogUnavailable, NativeFolderDialog
from kato_webserver.folder_dialog_routes import folder_dialog_available, register_folder_dialog_routes


class Program(object):
    """A stand-in for the dialog program: records each call, answers in order."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        answer = self.answers.pop(0) if self.answers else (0, '')
        if isinstance(answer, Exception):
            raise answer
        code, out = answer
        return subprocess.CompletedProcess(argv, code, stdout=out, stderr='')


def dialog(platform, program, *, tools=(), environ=None, wsl=False):
    found = {name: f'/bin/{name}' for name in tools}
    return NativeFolderDialog(platform=platform, which=found.get, run=program,
                              environ=environ if environ is not None else {}, is_wsl=lambda: wsl)


class MacTests(unittest.TestCase):
    def test_finder_choose_folder_with_the_start_and_prompt_as_arguments(self):
        program = Program((0, '/Users/me/dev/\n'))
        with tempfile.TemporaryDirectory() as start:
            picked = dialog('darwin', program, tools=['osascript']).choose(start=start, prompt='Pick it')
        self.assertEqual(picked, '/Users/me/dev')  # the trailing "/" dropped
        argv, kwargs = program.calls[0]
        self.assertEqual(argv[0], '/bin/osascript')
        self.assertIn('activate', argv)  # it comes to the front
        self.assertEqual(argv[-2:], [start, 'Pick it'])  # never pasted into the script
        self.assertIs(kwargs['stdin'], subprocess.DEVNULL)

    def test_a_cancel_is_none_and_a_missing_start_opens_its_nearest_parent(self):
        program = Program((1, ''))  # "User canceled. (-128)"
        with tempfile.TemporaryDirectory() as home:
            picked = dialog('darwin', program, tools=['osascript']).choose(start=f'{home}/code/new')
        self.assertIsNone(picked)
        self.assertEqual(program.calls[0][0][-2:], [home, nfd.DEFAULT_PROMPT])

    def test_no_osascript_no_dialog(self):
        mac = dialog('darwin', Program())
        self.assertFalse(mac.available())
        with self.assertRaises(FolderDialogUnavailable):
            mac.choose()


class WindowsTests(unittest.TestCase):
    def test_the_windows_dialog_through_powershell_with_its_values_in_the_environment(self):
        program = Program((0, 'C:\\Users\\me\\code'))
        picked = dialog('win32', program, tools=['powershell']).choose(prompt='Pick it')
        self.assertEqual(picked, 'C:\\Users\\me\\code')
        argv, kwargs = program.calls[0]
        self.assertEqual(argv[:5], ['/bin/powershell', '-NoProfile', '-NonInteractive', '-STA', '-Command'])
        self.assertIn('FolderBrowserDialog', argv[-1])
        self.assertEqual((kwargs['env']['FOLDER_DIALOG_PROMPT'], kwargs['env']['FOLDER_DIALOG_START']), ('Pick it', ''))
        self.assertTrue(dialog('win32', Program(), tools=['pwsh']).available())
        self.assertFalse(dialog('win32', Program()).available())

    def test_inside_wsl_the_windows_dialog_and_paths_turned_both_ways(self):
        program = Program((0, 'C:\\Users\\me\\home\n'), (0, 'C:\\Users\\me\\code\r\n'), (0, '/mnt/c/Users/me/code\n'))
        with tempfile.TemporaryDirectory() as start:
            picked = dialog('linux', program, tools=['powershell.exe', 'wslpath'], wsl=True).choose(start=start)
        self.assertEqual(picked, '/mnt/c/Users/me/code')
        self.assertEqual(program.calls[0][0], ['/bin/wslpath', '-w', start])
        self.assertEqual(program.calls[1][1]['env']['FOLDER_DIALOG_START'], 'C:\\Users\\me\\home')
        self.assertEqual(program.calls[2][0], ['/bin/wslpath', '-u', 'C:\\Users\\me\\code'])

    def test_inside_wsl_a_cancel_or_an_unconvertible_start_still_behaves(self):
        program = Program((1, ''), (1, ''))
        with tempfile.TemporaryDirectory() as start:
            picked = dialog('linux', program, tools=['powershell.exe', 'wslpath'], wsl=True).choose(start=start)
        self.assertIsNone(picked)
        self.assertEqual(program.calls[1][1]['env']['FOLDER_DIALOG_START'], '')
        self.assertFalse(dialog('linux', Program(), tools=['powershell.exe'], wsl=True).available())


class LinuxTests(unittest.TestCase):
    def test_zenity_first_then_kdialog_and_none_without_a_display(self):
        program = Program((0, '/home/me/src\n'))
        with tempfile.TemporaryDirectory() as start:
            picked = dialog('linux', program, tools=['zenity', 'kdialog'], environ={'DISPLAY': ':0'}).choose(start=start)
            self.assertEqual(picked, '/home/me/src')
            self.assertEqual(program.calls[0][0][:3], ['/bin/zenity', '--file-selection', '--directory'])
            self.assertIn(f'--filename={start}/', program.calls[0][0])
        kde = Program((0, '/home/me/work'))
        self.assertEqual(dialog('linux', kde, tools=['kdialog'], environ={'WAYLAND_DISPLAY': 'w'}).choose(), '/home/me/work')
        self.assertEqual(kde.calls[0][0][:3], ['/bin/kdialog', '--getexistingdirectory', '~'])
        self.assertFalse(dialog('linux', Program(), tools=['zenity']).available())  # a server: no screen
        self.assertFalse(dialog('linux', Program(), environ={'DISPLAY': ':0'}).available())
        no_start = Program((0, '/x'))
        dialog('linux', no_start, tools=['zenity'], environ={'DISPLAY': ':0'}).choose()
        self.assertFalse(any(arg.startswith('--filename') for arg in no_start.calls[0][0]))


class OneAtATimeTests(unittest.TestCase):
    def test_a_second_dialog_waits_for_none_it_is_refused(self):
        opened, release = threading.Event(), threading.Event()

        def slow(argv, **kwargs):
            opened.set()
            release.wait(5)
            return subprocess.CompletedProcess(argv, 0, stdout='/picked', stderr='')
        mac = dialog('darwin', slow, tools=['osascript'])
        answers = []
        worker = threading.Thread(target=lambda: answers.append(mac.choose()))
        worker.start()
        opened.wait(5)
        with self.assertRaises(FolderDialogBusy):
            mac.choose()
        release.set()
        worker.join(5)
        self.assertEqual(answers, ['/picked'])
        self.assertEqual(dialog('darwin', Program((0, '/again')), tools=['osascript']).choose(), '/again')

    def test_a_dialog_left_open_too_long_is_a_cancel(self):
        program = Program(subprocess.TimeoutExpired('osascript', nfd.DIALOG_TIMEOUT_SECONDS))
        self.assertIsNone(dialog('darwin', program, tools=['osascript']).choose())
        self.assertEqual(program.calls[0][1]['timeout'], nfd.DIALOG_TIMEOUT_SECONDS)


class WslDetectionTests(unittest.TestCase):
    def test_wsl_is_told_by_its_variable_or_its_kernel(self):
        from unittest import mock
        with mock.patch.dict(nfd.os.environ, {'WSL_DISTRO_NAME': 'Ubuntu'}):
            self.assertTrue(nfd._running_in_wsl())
        with mock.patch.dict(nfd.os.environ, {}, clear=True):
            with mock.patch.object(nfd.Path, 'read_text', return_value='Linux version 5.15 microsoft-standard-WSL2'):
                self.assertTrue(nfd._running_in_wsl())
            with mock.patch.object(nfd.Path, 'read_text', side_effect=OSError('no /proc')):
                self.assertFalse(nfd._running_in_wsl())
        self.assertEqual(nfd._existing_folder(''), '')
        self.assertEqual(nfd._existing_folder('relative/that/does/not/exist'), '')  # never where kato runs
        with mock.patch.object(nfd.Path, 'is_dir', return_value=False):  # a drive that is not there
            self.assertEqual(nfd._existing_folder('/nowhere/at/all'), '')
        self.assertIsInstance(NativeFolderDialog().available(), bool)  # the real computer's answer


class RoutesTests(unittest.TestCase):
    def client(self, choose_dialog):
        app = Flask(__name__)
        register_folder_dialog_routes(app, dialog=choose_dialog)
        return app, app.test_client()

    def test_available_and_a_pick_or_a_cancel(self):
        app, client = self.client(dialog('darwin', Program((0, '/Users/me/dev/'), (1, '')), tools=['osascript']))
        self.assertEqual(client.get('/api/fs/choose-folder').get_json(), {'available': True})
        self.assertTrue(folder_dialog_available(app))
        self.assertEqual(client.post('/api/fs/choose-folder', json={'prompt': 'Pick'}).get_json(), {'path': '/Users/me/dev'})
        self.assertEqual(client.post('/api/fs/choose-folder', json={}).get_json(), {'cancelled': True})

    def test_none_here_is_a_501_and_one_already_open_a_409(self):
        _, client = self.client(dialog('linux', Program()))
        response = client.post('/api/fs/choose-folder', json={})
        self.assertEqual((response.status_code, response.get_json()['error']), (501, 'no folder dialog can open on this computer'))
        busy = dialog('darwin', Program(), tools=['osascript'])
        busy._lock.acquire()
        _, client = self.client(busy)
        self.assertEqual(client.post('/api/fs/choose-folder', json={}).status_code, 409)
        self.assertFalse(folder_dialog_available(Flask('bare')))


if __name__ == '__main__':
    unittest.main()

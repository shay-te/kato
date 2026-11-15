"""The computer's own "choose a folder" dialog, opened by kato for its page.

A web page cannot open the system's folder dialog and learn the path picked —
browsers keep absolute paths from pages. But kato runs on the operator's
computer, so kato opens the REAL dialog there and hands the page the path. It
is kato's only folder picker ("Browse…"): there is no in-page browser.

* macOS — Finder's "Choose folder" (``osascript``; the script brings itself to
  the front first, and asks for no permission);
* Windows — the Windows folder dialog (PowerShell, on a top-most owner window);
* WSL (the desktop app's Windows backend) — the same Windows dialog, reached
  through WSL's interop, and the path turned into a Linux one (``wslpath``);
* Linux desktops — ``zenity``, else ``kdialog``; never with no display.

Where none can open (a server with no screen), ``available()`` says so and the
page asks for the folder's path to be typed. One dialog at a time. Every value reaches the
dialog as an argument or an environment variable, never pasted into a script.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

DIALOG_TIMEOUT_SECONDS = 600
DEFAULT_PROMPT = 'Choose a folder'

_APPLESCRIPT = (
    'on run argv',
    'activate',
    'set startFolder to item 1 of argv',
    'set promptText to item 2 of argv',
    'if startFolder is "" then',
    'return POSIX path of (choose folder with prompt promptText)',
    'end if',
    'return POSIX path of (choose folder with prompt promptText default location (POSIX file startFolder))',
    'end run',
)

_POWERSHELL = (
    'Add-Type -AssemblyName System.Windows.Forms; '
    '$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; '
    '$dialog.Description = $env:FOLDER_DIALOG_PROMPT; '
    '$dialog.ShowNewFolderButton = $true; '
    'if ($env:FOLDER_DIALOG_START) { $dialog.SelectedPath = $env:FOLDER_DIALOG_START }; '
    '$owner = New-Object System.Windows.Forms.Form -Property @{ TopMost = $true }; '
    'if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) '
    '{ [Console]::Out.Write($dialog.SelectedPath) }'
)


class FolderDialogBusy(Exception):
    """A folder dialog kato opened is still on screen."""


class FolderDialogUnavailable(Exception):
    """No folder dialog can open on this computer."""


class NativeFolderDialog(object):
    def __init__(
        self,
        *,
        platform: str = sys.platform,
        which: Callable[[str], str | None] = shutil.which,
        run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        environ: dict | None = None,
        is_wsl: Callable[[], bool] | None = None,
    ) -> None:
        self._platform = platform
        self._which = which
        self._run = run
        self._environ = os.environ if environ is None else environ
        self._is_wsl = is_wsl or _running_in_wsl
        self._lock = threading.Lock()

    def available(self) -> bool:
        return self._dialog() is not None

    def choose(self, *, start: str = '', prompt: str = DEFAULT_PROMPT) -> str | None:
        """The folder picked, or None when the operator cancelled."""
        dialog = self._dialog()
        if dialog is None:
            raise FolderDialogUnavailable('no folder dialog can open on this computer')
        if not self._lock.acquire(blocking=False):
            raise FolderDialogBusy('a folder dialog is already open — finish or cancel it first')
        try:
            return dialog(_existing_folder(start), str(prompt or DEFAULT_PROMPT))
        finally:
            self._lock.release()

    # ----- one per kind of computer -----

    def _dialog(self) -> Callable[[str, str], str | None] | None:
        if self._platform == 'darwin':
            return self._mac if self._which('osascript') else None
        if self._platform == 'win32':
            return self._windows if self._powershell() else None
        if self._is_wsl():
            return self._wsl if self._which('powershell.exe') and self._which('wslpath') else None
        if not (self._environ.get('DISPLAY') or self._environ.get('WAYLAND_DISPLAY')):
            return None
        if self._which('zenity'):
            return self._zenity
        return self._kdialog if self._which('kdialog') else None

    def _mac(self, start: str, prompt: str) -> str | None:
        argv = [self._which('osascript')]
        for line in _APPLESCRIPT:
            argv += ['-e', line]
        return self._picked(argv + [start, prompt])

    def _windows(self, start: str, prompt: str) -> str | None:
        return self._picked(self._powershell_argv(self._powershell()), start=start, prompt=prompt)

    def _wsl(self, start: str, prompt: str) -> str | None:
        windows_start = self._converted('-w', start) if start else ''
        picked = self._picked(self._powershell_argv(self._which('powershell.exe')),
                              start=windows_start, prompt=prompt)
        return self._converted('-u', picked) if picked else None

    def _zenity(self, start: str, prompt: str) -> str | None:
        argv = [self._which('zenity'), '--file-selection', '--directory', f'--title={prompt}']
        if start:
            argv.append(f'--filename={start.rstrip("/")}/')
        return self._picked(argv)

    def _kdialog(self, start: str, prompt: str) -> str | None:
        return self._picked([self._which('kdialog'), '--getexistingdirectory', start or '~', '--title', prompt])

    # ----- running them -----

    def _powershell(self) -> str | None:
        return self._which('powershell') or self._which('pwsh')

    @staticmethod
    def _powershell_argv(shell: str) -> list[str]:
        return [shell, '-NoProfile', '-NonInteractive', '-STA', '-Command', _POWERSHELL]

    def _picked(self, argv: list[str], *, start: str = '', prompt: str = '') -> str | None:
        """What the dialog printed, or None for a cancel (it exits non-zero)."""
        env = dict(self._environ)
        if start or prompt:
            env.update(FOLDER_DIALOG_START=start, FOLDER_DIALOG_PROMPT=prompt)
        try:
            result = self._run(argv, capture_output=True, text=True, timeout=DIALOG_TIMEOUT_SECONDS,
                               stdin=subprocess.DEVNULL, env=env)
        except subprocess.TimeoutExpired:
            return None
        picked = (result.stdout or '').strip()
        if result.returncode != 0 or not picked:
            return None
        return picked.rstrip('/\\') or picked

    def _converted(self, flag: str, path: str) -> str:
        result = self._run([self._which('wslpath'), flag, path], capture_output=True, text=True,
                           timeout=30, stdin=subprocess.DEVNULL)
        return (result.stdout or '').strip() if result.returncode == 0 else ''


def _existing_folder(start: str) -> str:
    """``start`` when it is a folder that exists, else its nearest existing
    parent, else '' — a dialog asked to open a missing folder may refuse.
    Only an absolute folder: a relative one would open wherever kato runs."""
    text = str(start or '').strip()
    if not text:
        return ''
    path = Path(text).expanduser()
    if not path.is_absolute():
        return ''
    for candidate in (path, *path.parents):
        if candidate.is_dir():
            return str(candidate)
    return ''


def _running_in_wsl() -> bool:
    if os.environ.get('WSL_DISTRO_NAME'):
        return True
    try:
        return 'microsoft' in Path('/proc/version').read_text(encoding='utf-8').lower()
    except OSError:
        return False

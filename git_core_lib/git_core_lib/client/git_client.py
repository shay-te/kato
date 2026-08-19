"""GitClientMixin — provider-agnostic git subprocess engine.

Mixin class providing all git subprocess operations. Subclasses must
supply ``self.logger``. HTTP auth injection is a hook:
override ``_build_git_http_auth_header(repository)`` to return a
non-empty header string when the repository uses an HTTP remote.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from git_core_lib.git_core_lib.helpers.git_command_utils import (
    build_safe_git_command,
    safe_directory_args,
)
from utils_core_lib.utils_core_lib.text_utils import normalized_lower_text


class GitClientMixin:
    """Mixin providing git subprocess operations for any service class.

    Requirements for the host class:
    - Must have a ``self.logger`` attribute (``logging.Logger``).
    - May override ``_build_git_http_auth_header(repository) -> str``
      to inject provider-specific HTTP auth headers.
    """

    GIT_SUBPROCESS_TIMEOUT_SECONDS = 300
    NON_FAST_FORWARD_PUSH_REJECTION_MARKERS = (
        'fetch first',
        'non-fast-forward',
        'updates were rejected because the remote contains work',
    )

    # ----- hook for subclasses -----

    def _build_git_http_auth_header(self, repository) -> str:
        """Return an HTTP ``Authorization`` header for git HTTP remotes.

        Default returns '' (no auth). Subclasses override to inject
        provider-specific credentials (e.g. Basic auth for Bitbucket).
        """
        return ''

    # ----- subprocess core -----

    @staticmethod
    def _validate_git_executable() -> None:
        if shutil.which('git'):
            return
        raise RuntimeError('git executable is required but was not found on PATH')

    @staticmethod
    def _git_safe_directory_args(local_path: str) -> list[str]:
        return safe_directory_args(local_path)

    @classmethod
    def _git_command(cls, local_path: str, args: list[str]) -> list[str]:
        # Delegates to the shared helper (git_command_utils.py) so every
        # git-invoking module in the codebase gets the SAME hook-disabling
        # protection from one place — a second, independently-written git
        # helper once duplicated this logic inline and shipped without it.
        return build_safe_git_command(local_path, args)

    @classmethod
    def _run_capture(cls, cmd: list[str], *, env=None):
        """Run ``cmd`` capturing text output, never raising on a
        non-zero exit. The shared kwargs for every plain-capture
        subprocess invocation in this mixin live here."""
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=False,
            env=env,
            timeout=cls.GIT_SUBPROCESS_TIMEOUT_SECONDS,
        )

    @staticmethod
    def _failure_detail(result) -> str:
        return GitClientMixin._condense_git_output(
            result.stderr.strip() or result.stdout.strip() or 'git command failed',
        )

    # Lines git emits as PROGRESS, not as information. A long rebase
    # produces one per commit — 261 of them in one observed failure —
    # and every one ended up inside the RuntimeError message, so the
    # actual cause ("could not apply <sha>") scrolled off the top of a
    # wall of "Rebasing (n/N)". Dropped entirely; the counts tell the
    # reader nothing once the command has already failed.
    _PROGRESS_LINE_PATTERNS = (
        re.compile(r'^Rebasing \(\d+/\d+\)$'),
        re.compile(r'^Applying: '),
        re.compile(r'^remote: (Counting|Compressing|Total|Resolving) '),
        re.compile(r'^(Receiving|Resolving|Counting|Compressing) objects:'),
        re.compile(r'^Updating files:'),
        re.compile(r'^\s*$'),
    )
    # Enough to carry the error plus git's hint block; beyond this the
    # message stops being read.
    _MAX_DETAIL_LINES = 18

    @classmethod
    def _condense_git_output(cls, text: str) -> str:
        """Strip progress spam and cap the length of a git failure message."""
        kept = [
            line for line in str(text or '').splitlines()
            if not any(p.match(line) for p in cls._PROGRESS_LINE_PATTERNS)
        ]
        if not kept:
            return str(text or '').strip()[:400] or 'git command failed'
        if len(kept) > cls._MAX_DETAIL_LINES:
            dropped = len(kept) - cls._MAX_DETAIL_LINES
            kept = kept[-cls._MAX_DETAIL_LINES:]
            kept.insert(0, f'… {dropped} earlier line(s) omitted')
        return '\n'.join(kept).strip()

    @staticmethod
    def _configured_remote_url(repository) -> str:
        """The remote URL from the TRUSTED config, not from ``.git/config``."""
        return str(getattr(repository, 'remote_url', '') or '').strip()

    @classmethod
    def _auth_header_config_key(cls, repository) -> str:
        """``http.<configured-url>.extraHeader``, or the bare key.

        Falls back to the unscoped key only when no remote URL is
        configured — in that case there is nothing to scope to, and the
        header builder would have had no repository to work from either.
        """
        url = cls._configured_remote_url(repository)
        return f'http.{url}.extraHeader' if url else 'http.extraHeader'

    @staticmethod
    def _normalized_remote_url(value: str) -> str:
        """Compare-able form: no credentials, no trailing ``/`` or ``.git``."""
        text = str(value or '').strip()
        if '@' in text and '://' in text:
            scheme, _, rest = text.partition('://')
            text = f'{scheme}://{rest.rpartition("@")[2]}'
        text = text.rstrip('/')
        if text.endswith('.git'):
            text = text[:-len('.git')]
        return text.lower()

    def _assert_remote_is_the_configured_one(self, local_path: str, repository) -> None:
        """Refuse to talk to a remote the agent redirected.

        ``origin`` lives in the workspace clone's ``.git/config``, which
        the agent can write, and ``url.<base>.insteadOf`` can rewrite even
        an explicitly passed URL. ``ls-remote --get-url`` asks git for the
        URL it would ACTUALLY use, after every rewrite — comparing that to
        the configured one is the only check that sees what git sees.
        """
        configured = self._configured_remote_url(repository)
        if not configured:
            return
        result = self._run_capture(
            self._git_command(local_path, ['ls-remote', '--get-url', 'origin']),
        )
        effective = (result.stdout or '').strip()
        if not effective:
            return
        if self._normalized_remote_url(effective) != self._normalized_remote_url(configured):
            raise RuntimeError(
                f'refusing to use remote {effective!r} for repository at '
                f'{local_path}: the configured remote is {configured!r}. The '
                f'workspace ``.git/config`` is agent-writable, so a changed '
                f'remote (or a url.*.insteadOf rewrite) is treated as an '
                f'attempt to redirect credentials, not as a configuration '
                f'change.',
            )

    def _run_git_subprocess(
        self,
        local_path: str,
        args: list[str],
        repository=None,
    ):
        env = os.environ.copy()
        env['GIT_TERMINAL_PROMPT'] = '0'
        auth_header = self._build_git_http_auth_header(repository)
        if auth_header:
            env['GIT_CONFIG_COUNT'] = '1'
            # SCOPE the credential to the configured remote URL. A bare
            # ``http.extraHeader`` applies to every host git contacts in
            # this invocation, and the remote it contacts comes from the
            # AGENT-WRITABLE ``.git/config`` — so an agent that repointed
            # ``origin`` (or added a ``url.<x>.insteadOf`` rewrite) would
            # be handed the provider token the moment an operator approved
            # a push. URL-scoped config makes git send the header only to
            # the URL the caller configured.
            env['GIT_CONFIG_KEY_0'] = self._auth_header_config_key(repository)
            env['GIT_CONFIG_VALUE_0'] = auth_header
        else:
            env.pop('GIT_CONFIG_COUNT', None)
            env.pop('GIT_CONFIG_KEY_0', None)
            env.pop('GIT_CONFIG_VALUE_0', None)
        return self._run_capture(self._git_command(local_path, args), env=env)

    def _run_git(
        self,
        local_path: str,
        args: list[str],
        failure_message: str,
        repository=None,
    ):
        self._validate_git_executable()
        result = self._run_git_subprocess(local_path, args, repository)
        if result.returncode == 0:
            return result
        failure_detail = self._failure_detail(result)
        if self._is_git_index_lock_error(failure_detail) and self._clear_stale_git_index_lock(
            local_path
        ):
            result = self._run_git_subprocess(local_path, args, repository)
            if result.returncode == 0:
                return result
            failure_detail = self._failure_detail(result)
        raise RuntimeError(f'{failure_message}: {failure_detail}')

    def _git_stdout(
        self,
        local_path: str,
        args: list[str],
        failure_message: str,
        repository=None,
    ) -> str:
        result = self._run_git(local_path, args, failure_message, repository)
        return result.stdout.strip()

    def git_grep(
        self,
        local_path: str,
        query: str,
        *,
        limit: int = 200,
        repository=None,
    ) -> list[dict]:
        """Content search via ``git grep`` over tracked files in a repo.

        Returns ``[{path, line, text}]`` (repo-relative paths), fixed-string
        + case-insensitive, binary files skipped (``-I``), capped at
        ``limit`` lines. ``git grep`` exits 1 on "no matches" — that's NOT
        an error here; only other non-zero codes raise.
        """
        normalized = str(query or '').strip()
        if not normalized:
            return []
        self._validate_git_executable()
        result = self._run_git_subprocess(
            local_path,
            # ``--untracked`` so the agent's brand-new (uncommitted) files
            # are searchable too; .gitignore is still respected.
            ['grep', '--no-color', '-n', '-I', '-i', '-F', '--untracked', '-e', normalized],
            repository,
        )
        if result.returncode not in (0, 1):
            raise RuntimeError(f'git grep failed: {self._failure_detail(result)}')
        matches: list[dict] = []
        for raw_line in (result.stdout or '').splitlines():
            # ``path:line:text`` — split only twice so colons in ``text`` survive.
            parts = raw_line.split(':', 2)
            if len(parts) < 3:
                continue
            try:
                line_no = int(parts[1])
            except ValueError:
                continue
            matches.append({'path': parts[0], 'line': line_no, 'text': parts[2]})
            if len(matches) >= limit:
                break
        return matches

    # ----- reference / status queries -----

    def _git_reference_exists(self, local_path: str, reference: str) -> bool:
        result = self._run_capture(
            self._git_command(local_path, ['rev-parse', '--verify', reference]),
        )
        return result.returncode == 0

    def _left_right_commit_counts(
        self,
        local_path: str,
        left_reference: str,
        right_reference: str,
    ) -> tuple[int, int]:
        counts_text = self._git_stdout(
            local_path,
            ['rev-list', '--left-right', '--count', f'{left_reference}...{right_reference}'],
            f'failed to compare {left_reference} against {right_reference}',
        )
        parts = counts_text.split()
        if len(parts) != 2:
            raise RuntimeError(
                f'failed to parse commit counts for {left_reference}...{right_reference}: '
                f'{counts_text or "<empty>"}'
            )
        try:
            return int(parts[0]), int(parts[1])
        except ValueError as exc:
            raise RuntimeError(
                f'failed to parse commit counts for {left_reference}...{right_reference}: '
                f'{counts_text or "<empty>"}'
            ) from exc

    def _ahead_count(
        self,
        local_path: str,
        comparison_ref: str,
        branch_name: str,
    ) -> int:
        ahead_count_text = self._git_stdout(
            local_path,
            ['rev-list', '--count', f'{comparison_ref}..{branch_name}'],
            f'failed to compare branch {branch_name} against {comparison_ref}',
        )
        try:
            return int(ahead_count_text or '0')
        except ValueError as exc:
            raise RuntimeError(
                f'failed to parse ahead count for branch {branch_name}: '
                f'{ahead_count_text or "<empty>"}'
            ) from exc

    def _current_branch(self, local_path: str) -> str:
        return self._git_stdout(
            local_path,
            ['rev-parse', '--abbrev-ref', 'HEAD'],
            f'failed to determine current branch for {local_path}',
        )

    def _working_tree_status(self, local_path: str) -> str:
        return self._git_stdout(
            local_path,
            ['status', '--porcelain'],
            f'failed to inspect working tree for repository at {local_path}',
        )

    # ----- index lock recovery -----

    @staticmethod
    def _is_git_index_lock_error(error_text: str) -> bool:
        normalized = normalized_lower_text(error_text)
        return 'index.lock' in normalized and 'file exists' in normalized

    def _clear_stale_git_index_lock(self, local_path: str) -> bool:
        lock_path = Path(local_path) / '.git' / 'index.lock'
        if self._has_running_git_process(local_path):
            self.logger.warning(
                'leaving git index lock in place at %s because another git process is still running',
                lock_path,
            )
            return False
        try:
            lock_path.unlink()
        except FileNotFoundError:
            return False
        self.logger.warning('removed stale git index lock at %s', lock_path)
        return True

    @staticmethod
    def _has_running_git_process(local_path: str) -> bool:
        try:
            result = subprocess.run(
                ['ps', '-eo', 'command='],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                check=False,
                timeout=GitClientMixin.GIT_SUBPROCESS_TIMEOUT_SECONDS,
            )
        except OSError:
            return False
        if result.returncode != 0:
            return False
        repository_arg = f'-C {local_path}'
        for command_line in result.stdout.splitlines():
            normalized_command_line = command_line.strip()
            if not normalized_command_line.startswith('git '):
                continue
            if repository_arg in normalized_command_line:
                return True
        return False

    # ----- push / sync / rebase -----

    def _push_branch(
        self,
        local_path: str,
        branch_name: str,
        repository=None,
        *,
        dry_run: bool = False,
    ) -> None:
        # Verify BEFORE the push: this is the operation that carries the
        # provider token, and an approved push to a redirected remote
        # hands that token to whoever owns it.
        self._assert_remote_is_the_configured_one(local_path, repository)
        push_args = ['push']
        if dry_run:
            push_args.append('--dry-run')
        push_args.extend(['-u', 'origin', branch_name])
        try:
            self._run_git(local_path, push_args, f'failed to push branch {branch_name}', repository)
        except RuntimeError as exc:
            if dry_run or not self._is_non_fast_forward_push_rejection(exc):
                raise
            self.logger.warning(
                'push for branch %s was rejected because origin has newer commits; '
                'fetching and rebasing before retrying',
                branch_name,
            )
            self._sync_branch_with_remote(local_path, branch_name, repository)
            self._run_git(
                local_path,
                push_args,
                f'failed to push branch {branch_name} after syncing with origin/{branch_name}',
                repository,
            )

    def _sync_branch_with_remote(
        self, local_path: str, branch_name: str, repository=None
    ) -> None:
        remote_branch_ref = f'refs/remotes/origin/{branch_name}'
        remote_branch = f'origin/{branch_name}'
        self._run_git(
            local_path,
            ['fetch', 'origin', f'{branch_name}:{remote_branch_ref}'],
            f'failed to fetch latest {remote_branch} before pushing {branch_name}',
            repository,
        )
        if not self._git_reference_exists(local_path, remote_branch):
            raise RuntimeError(
                f'failed to fetch latest {remote_branch} before pushing {branch_name}: '
                f'{remote_branch} is not available locally'
            )
        self._rebase_branch_onto_remote(local_path, branch_name, remote_branch, repository)

    def _rebase_branch_onto_remote(
        self,
        local_path: str,
        branch_name: str,
        remote_branch: str,
        repository=None,
    ) -> None:
        try:
            self._run_git(
                local_path,
                ['rebase', remote_branch],
                f'failed to rebase branch {branch_name} onto {remote_branch}',
                repository,
            )
        except RuntimeError:
            self._abort_rebase_after_failure(local_path, branch_name, repository)
            raise

    def _abort_rebase_after_failure(
        self,
        local_path: str,
        branch_name: str,
        repository=None,
    ) -> None:
        try:
            self._run_git(
                local_path,
                ['rebase', '--abort'],
                f'failed to abort rebase for branch {branch_name}',
                repository,
            )
        except RuntimeError as abort_exc:
            self.logger.warning(
                'failed to abort rebase for branch %s after push-sync failure: %s',
                branch_name,
                abort_exc,
            )

    @classmethod
    def _is_non_fast_forward_push_rejection(cls, exc: RuntimeError) -> bool:
        message = normalized_lower_text(str(exc))
        return any(marker in message for marker in cls.NON_FAST_FORWARD_PUSH_REJECTION_MARKERS)

    def _pull_destination_branch(
        self,
        local_path: str,
        destination_branch: str,
        repository=None,
    ) -> None:
        self._run_git(
            local_path,
            ['pull', '--ff-only', 'origin', destination_branch],
            f'failed to pull latest {destination_branch} for repository at {local_path}',
            repository,
        )

    # ----- misc -----

    @staticmethod
    def _uses_http_remote(remote_url: str) -> bool:
        normalized = normalized_lower_text(remote_url)
        return normalized.startswith('https://') or normalized.startswith('http://')

    @classmethod
    def _infer_default_branch(cls, local_path: str) -> str:
        cls._validate_git_executable()
        commands = [
            ['symbolic-ref', 'refs/remotes/origin/HEAD'],
            ['branch', '--show-current'],
        ]
        for command in commands:
            result = cls._run_capture(cls._git_command(local_path, command))
            output = result.stdout.strip()
            if result.returncode != 0 or not output:
                continue
            if output.startswith('refs/remotes/'):
                return output.rsplit('/', 1)[-1]
            return output
        raise ValueError(
            f'unable to determine destination branch for repository at {local_path}'
        )

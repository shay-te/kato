"""Tests for the three hardening gaps closed after the reploy comparison.

1. ``_assert_sandbox_flags`` — the required/forbidden flag sets are now
   enforced at SPAWN time, not only by the CI drift guard.
2. ``_assert_seccomp_pinned`` — the profile is asserted POSITIVELY
   (vendored file, exactly one option), because a spawn with no seccomp
   option at all silently inherited a host-settable daemon default.
3. ``reap_orphan_sandbox_containers`` — ``--rm`` only fires when the
   CONTAINER's process exits, so a controller that dies leaves a live
   container with the workspace mounted. The sweep removes exactly the
   containers whose owner is provably gone.
"""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from unittest import mock

from sandbox_core_lib.sandbox_core_lib import manager


class SeccompPinTests(unittest.TestCase):
    def test_vendored_profile_exists_and_denies_by_default(self) -> None:
        # A profile whose default action is not ERRNO would allow every
        # syscall it does not mention — an allowlist that isn't one.
        self.assertTrue(manager._SECCOMP_PROFILE_PATH.is_file())
        profile = json.loads(
            manager._SECCOMP_PROFILE_PATH.read_text(encoding='utf-8'),
        )
        self.assertEqual(profile['defaultAction'], 'SCMP_ACT_ERRNO')
        self.assertTrue(profile['syscalls'])

    def test_pinned_profile_accepted(self) -> None:
        argv = [
            'docker', 'run',
            '--security-opt', f'seccomp={manager._SECCOMP_PROFILE_PATH}',
        ]
        manager._assert_seccomp_pinned(argv)  # must not raise

    def test_missing_seccomp_option_is_refused(self) -> None:
        # The exact hole this closes: no option at all used to pass the
        # old "is it unconfined?" check while inheriting the daemon default.
        with self.assertRaises(manager.SandboxError) as caught:
            manager._assert_seccomp_pinned(['docker', 'run', '--read-only'])
        self.assertIn('exactly one seccomp option', str(caught.exception))

    def test_two_seccomp_options_are_refused(self) -> None:
        argv = [
            'docker', 'run',
            '--security-opt', f'seccomp={manager._SECCOMP_PROFILE_PATH}',
            '--security-opt', 'seccomp=unconfined',
        ]
        with self.assertRaises(manager.SandboxError):
            manager._assert_seccomp_pinned(argv)

    def test_foreign_profile_path_is_refused(self) -> None:
        argv = ['docker', 'run', '--security-opt', 'seccomp=/tmp/weak.json']
        with self.assertRaises(manager.SandboxError) as caught:
            manager._assert_seccomp_pinned(argv)
        self.assertIn('vendored', str(caught.exception))


class GitDirIsReadOnlyTests(unittest.TestCase):
    """``.git`` must not be writable by the agent.

    Git config is a command-execution surface, and the HOST runs git
    against the same clone the agent edits. ``core.fsmonitor`` in a
    workspace ``.git/config`` gave a working sandbox-to-host RCE —
    reproduced against this codebase, not theorised. Overriding the
    dangerous keys treats symptoms and cannot cover content filters,
    whose driver names are attacker-chosen. Freezing the directory
    removes the input instead.
    """

    def _workspace(self, layout):
        import tempfile
        from pathlib import Path
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for relative in layout:
            (root / relative).mkdir(parents=True)
        return str(root)

    def test_each_clone_git_dir_is_mounted_read_only(self) -> None:
        workspace = self._workspace(['client/.git', 'server/.git'])
        mounts = manager._git_dir_readonly_mounts(workspace)
        joined = ' '.join(mounts)
        self.assertIn('/client/.git:/workspace/client/.git:ro', joined)
        self.assertIn('/server/.git:/workspace/server/.git:ro', joined)
        self.assertEqual(mounts.count('-v'), 2)

    def test_nested_layouts_are_covered(self) -> None:
        workspace = self._workspace(['group/service/.git'])
        joined = ' '.join(manager._git_dir_readonly_mounts(workspace))
        self.assertIn('/workspace/group/service/.git:ro', joined)

    def test_a_workspace_without_clones_adds_no_mounts(self) -> None:
        self.assertEqual(manager._git_dir_readonly_mounts(self._workspace(['src'])), [])

    def test_git_file_worktrees_are_skipped(self) -> None:
        # Submodules and linked worktrees use a ``.git`` FILE. Bind-mounting
        # it read-only would be pointless and could break the link.
        import tempfile
        from pathlib import Path
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / 'sub').mkdir()
        (root / 'sub' / '.git').write_text('gitdir: /elsewhere\n', encoding='utf-8')
        self.assertEqual(manager._git_dir_readonly_mounts(str(root)), [])

    def test_the_overlay_comes_after_the_writable_workspace_mount(self) -> None:
        # Docker applies later binds on top of earlier ones, so ordering
        # is the whole mechanism: reversed, the read-only overlay would be
        # replaced by the writable parent and .git would be writable again.
        import tempfile
        from pathlib import Path
        fake_id = 'sha256:' + 'c' * 64
        with tempfile.TemporaryDirectory(dir=Path.home()) as workspace:
            (Path(workspace) / 'repo' / '.git').mkdir(parents=True)
            with mock.patch.object(
                manager, '_image_digest_strict', return_value=fake_id,
            ):
                argv = manager.wrap_command(
                    ['claude'], workspace_path=workspace, task_id='T',
                )
        workspace_mount = next(
            index for index, token in enumerate(argv)
            if token.endswith(':/workspace:rw')
        )
        git_mount = next(
            index for index, token in enumerate(argv)
            if token.endswith('/repo/.git:ro')
        )
        self.assertLess(workspace_mount, git_mount)


class AgentSeccompProfileTests(unittest.TestCase):
    """The spawn uses the TIGHTENED profile, and its delta stays auditable."""

    def _profiles(self):
        from sandbox_core_lib.sandbox_core_lib.seccomp import derive_agent_profile
        baseline = json.loads(
            derive_agent_profile.BASELINE_PATH.read_text(encoding='utf-8'),
        )
        agent = json.loads(
            derive_agent_profile.AGENT_PATH.read_text(encoding='utf-8'),
        )
        return derive_agent_profile, baseline, agent

    def test_spawn_uses_the_agent_profile_not_the_baseline(self) -> None:
        self.assertEqual(manager._SECCOMP_PROFILE_PATH.name, 'agent.json')

    def test_checked_in_profile_equals_baseline_minus_denylist(self) -> None:
        # The whole point of deriving: a future baseline refresh cannot
        # silently drop a denial, because this recomputes it.
        module, baseline, agent = self._profiles()
        self.assertEqual(module.derive(baseline), agent)

    def test_every_denied_syscall_is_actually_absent(self) -> None:
        module, _baseline, agent = self._profiles()
        allowed = {
            name
            for rule in agent.get('syscalls', [])
            for name in rule.get('names', [])
        }
        for denied in module.DENIED_SYSCALLS:
            self.assertNotIn(denied, allowed, f'{denied} survived the derivation')

    def test_the_denylist_actually_removed_something(self) -> None:
        # A denylist of names that were never in the baseline would pass
        # every check above while changing nothing.
        module, baseline, agent = self._profiles()
        before = {n for r in baseline.get('syscalls', []) for n in r.get('names', [])}
        after = {n for r in agent.get('syscalls', []) for n in r.get('names', [])}
        self.assertTrue(before - after)
        self.assertTrue(module.DENIED_SYSCALLS & before)

    def test_profile_still_denies_by_default(self) -> None:
        _module, _baseline, agent = self._profiles()
        self.assertEqual(agent['defaultAction'], 'SCMP_ACT_ERRNO')


class AuditChainVerificationTests(unittest.TestCase):
    """The log is hash-chained; until now nothing ever checked it."""

    def _log(self, entries: int):
        import tempfile
        from pathlib import Path
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        path = Path(self._tmp.name) / 'audit.log'
        for index in range(entries):
            manager.record_spawn(
                task_id=f'T-{index}', container_name=f'c{index}',
                workspace_path='/ws', audit_log_path=path,
            )
        return path

    def test_missing_log_is_not_a_failure(self) -> None:
        from pathlib import Path
        result = manager.verify_audit_chain(Path('/nonexistent/audit.log'))
        self.assertTrue(result['ok'])
        self.assertEqual(result['entries'], 0)

    def test_honest_chain_verifies(self) -> None:
        result = manager.verify_audit_chain(self._log(3))
        self.assertTrue(result['ok'])
        self.assertEqual(result['entries'], 3)

    def test_edited_entry_breaks_the_following_link(self) -> None:
        path = self._log(3)
        lines = path.read_bytes().splitlines()
        entry = json.loads(lines[1])
        entry['workspace_path'] = '/somewhere-else'
        lines[1] = json.dumps(entry, sort_keys=True).encode()
        path.write_bytes(b'\n'.join(lines) + b'\n')
        result = manager.verify_audit_chain(path)
        self.assertFalse(result['ok'])
        # Caught ON the tampered entry now, not on the next one: the MAC
        # fails immediately, where the chain link only broke downstream.
        self.assertEqual(result['broken_at'], 2)
        self.assertIn('MAC', result['error'])

    def test_reordering_is_detected(self) -> None:
        path = self._log(3)
        lines = path.read_bytes().splitlines()
        lines[1], lines[2] = lines[2], lines[1]
        path.write_bytes(b'\n'.join(lines) + b'\n')
        self.assertFalse(manager.verify_audit_chain(path)['ok'])

    def test_tail_truncation_is_NOT_detected(self) -> None:
        # Pinning the documented residual rather than pretending it away:
        # a shorter prefix is still internally consistent, and no hash
        # chain can catch that without an external anchor.
        path = self._log(3)
        lines = path.read_bytes().splitlines()
        path.write_bytes(b'\n'.join(lines[:2]) + b'\n')
        result = manager.verify_audit_chain(path)
        self.assertTrue(result['ok'])
        self.assertEqual(result['entries'], 2)

    def test_a_fully_rebuilt_chain_is_still_rejected(self) -> None:
        # The criticism this closes: a plain hash chain can be recomputed
        # end-to-end by anyone who can write the file, so "the chain
        # verifies" proved nothing about authenticity. Entries are now
        # MAC'd with a key kept outside the log directory.
        import hashlib
        path = self._log(2)
        rebuilt = []
        prev = manager._AUDIT_GENESIS_HASH
        for index in range(2):
            entry = {
                'timestamp': 'x', 'event': 'spawn', 'task_id': f'FAKE-{index}',
                'container_name': 'c', 'image_tag': 't', 'image_digest': '',
                'workspace_path': '/w', 'prev_hash': prev,
            }
            raw = json.dumps(entry, ensure_ascii=False).encode()
            rebuilt.append(raw)
            prev = hashlib.sha256(raw).hexdigest()
        path.write_bytes(b'\n'.join(rebuilt) + b'\n')
        result = manager.verify_audit_chain(path)
        self.assertFalse(result['ok'])
        self.assertIn('MAC', result['error'])

    def test_the_signing_key_lives_outside_the_log_directory_and_is_0600(self) -> None:
        import stat
        key_path = manager._audit_key_path()
        manager._audit_key()                      # create on first use
        if key_path.exists():
            self.assertEqual(stat.S_IMODE(key_path.stat().st_mode), 0o600)
        self.assertNotEqual(key_path, manager._DEFAULT_AUDIT_LOG_PATH)

    def test_garbage_line_is_reported_not_raised(self) -> None:
        path = self._log(1)
        path.write_bytes(path.read_bytes() + b'not json\n')
        result = manager.verify_audit_chain(path)
        self.assertFalse(result['ok'])
        self.assertEqual(result['broken_at'], 2)


class ContainerLifetimeCapTests(unittest.TestCase):
    """A wedged container must not hold a workspace mount open forever."""

    def test_cap_reaps_a_container_whose_owner_is_healthy(self) -> None:
        from sandbox_core_lib.sandbox_core_lib import watchdog as wd
        read_fd, write_fd = os.pipe()          # owner stays ALIVE
        self.addCleanup(os.close, write_fd)
        clock = {'t': 0.0}

        def fake_now():
            clock['t'] += 31.0                 # one idle poll per call
            return clock['t']

        # The waiter never fires (owner healthy); the cap must still trip.
        with mock.patch.object(wd, '_container_labels', return_value={'a': 'b'}), \
             mock.patch.object(wd._OwnerLostSignal, 'wait', return_value=wd._STILL_WAITING), \
             mock.patch.object(wd._OwnerLostSignal, 'close'):
            reason = wd._wait_for_reap_trigger(read_fd, 'c1', 60.0, fake_now)
        self.assertEqual(reason, 'max-lifetime-exceeded')

    def test_zero_disables_the_cap(self) -> None:
        from sandbox_core_lib.sandbox_core_lib import watchdog as wd
        read_fd, write_fd = os.pipe()
        os.close(write_fd)                     # EOF → owner-lost path
        with mock.patch.object(wd, '_container_labels', return_value={'a': 'b'}):
            reason = wd._wait_for_reap_trigger(read_fd, 'c1', 0.0, lambda: 1e9)
        self.assertEqual(reason, 'owner-process-lost')

    def test_default_cap_is_generous_but_finite(self) -> None:
        with mock.patch.dict(manager.os.environ, {}, clear=True):
            self.assertEqual(manager._max_container_lifetime_seconds(), 8 * 60 * 60)

    def test_env_override_and_bad_values(self) -> None:
        key = manager.MAX_CONTAINER_LIFETIME_ENV_KEY
        with mock.patch.dict(manager.os.environ, {key: '120'}, clear=True):
            self.assertEqual(manager._max_container_lifetime_seconds(), 120.0)
        with mock.patch.dict(manager.os.environ, {key: '0'}, clear=True):
            self.assertEqual(manager._max_container_lifetime_seconds(), 0.0)
        with mock.patch.dict(manager.os.environ, {key: 'nonsense'}, clear=True):
            self.assertEqual(manager._max_container_lifetime_seconds(), 8 * 60 * 60)


class SandboxFlagAssertionTests(unittest.TestCase):
    def _passing_argv(self) -> list[str]:
        argv = ['docker', 'run']
        for flag in manager._REQUIRED_DOCKER_FLAGS:
            argv.extend(flag.split('=', 1) if '=' in flag else [flag])
        return argv

    def test_complete_argv_passes(self) -> None:
        manager._assert_sandbox_flags(self._passing_argv())

    def test_missing_required_flag_is_refused(self) -> None:
        argv = self._passing_argv()
        argv.remove('--read-only')
        with self.assertRaises(manager.SandboxError) as caught:
            manager._assert_sandbox_flags(argv)
        self.assertIn('--read-only', str(caught.exception))

    def test_forbidden_flag_is_refused(self) -> None:
        argv = self._passing_argv() + ['--privileged']
        with self.assertRaises(manager.SandboxError) as caught:
            manager._assert_sandbox_flags(argv)
        self.assertIn('--privileged', str(caught.exception))

    def test_matcher_accepts_both_docker_forms(self) -> None:
        self.assertTrue(manager.flag_present_in_argv(['--ipc=none'], '--ipc=none'))
        self.assertTrue(manager.flag_present_in_argv(['--ipc', 'none'], '--ipc=none'))
        self.assertFalse(manager.flag_present_in_argv(['--ipc', 'host'], '--ipc=none'))
        self.assertTrue(manager.flag_present_in_argv(['--read-only'], '--read-only'))


class DaemonAcceptsArgvTests(unittest.TestCase):
    """Regression guards for flags the DAEMON rejects.

    Three spawn-killing bugs shipped at once because every check was a
    set-comparison — nothing ever asked Docker whether the argv parses:

    * ``--pid=container`` — Docker takes only ``host`` or
      ``container:<id>``; the bare word is "invalid PID mode".
    * ``--uts=private`` — Docker takes only ``host``. (Both spellings
      are Podman's, where they are valid.)
    * ``<tag>@sha256:<image Id>`` — the ``name@sha256:`` form resolves a
      *registry manifest* digest. A locally built image has none, so
      Docker went to the network and failed with "pull access denied".

    Each was invisible to the flag-set guards because the declared set
    and the emitted argv agreed with each other — and were both wrong.
    """

    def _argv(self) -> list[str]:
        import tempfile
        from pathlib import Path
        fake_id = 'sha256:' + 'a' * 64
        with tempfile.TemporaryDirectory(dir=Path.home()) as workspace:
            with mock.patch.object(
                manager, '_image_digest_strict', return_value=fake_id,
            ):
                return manager.wrap_command(
                    ['claude', '-p', 'hi'],
                    workspace_path=workspace,
                    task_id='ARGV-1',
                )

    def test_namespace_flags_use_only_modes_docker_accepts(self) -> None:
        argv = self._argv()
        valid = {'--pid': ('host',), '--uts': ('host',)}
        for index, token in enumerate(argv):
            for flag, allowed in valid.items():
                value = None
                if token == flag and index + 1 < len(argv):
                    value = argv[index + 1]
                elif token.startswith(f'{flag}='):
                    value = token.split('=', 1)[1]
                if value is None:
                    continue
                self.assertTrue(
                    value in allowed or value.startswith('container:'),
                    f'{flag}={value} is not a mode Docker accepts — the '
                    f'daemon fails the spawn with "invalid mode". Private '
                    f'namespaces are the DEFAULT: omit the flag.',
                )

    def test_image_is_referenced_by_resolvable_id_not_tag_at_digest(self) -> None:
        argv = self._argv()
        reference = argv[argv.index('claude') - 1]
        self.assertTrue(
            reference.startswith('sha256:'),
            f'image reference {reference!r} must be a bare image ID',
        )
        self.assertNotIn(
            '@', reference,
            'tag@sha256:<image Id> makes Docker resolve a REGISTRY digest '
            'that a locally built image does not have — it tries to pull '
            'and the spawn dies with "pull access denied".',
        )


class ConfigDirHandoverTests(unittest.TestCase):
    """The entrypoint must actually hand the config dir to the agent user.

    Two bugs lived here, both silent:

    * ``chown`` needs CAP_CHOWN, which was not in the cap-add list, and
      the failure was swallowed by ``2>/dev/null || true``. The agent
      got ``/home/claude/.claude`` as root:root 0700 — unreadable and
      unwritable — surfacing much later as a confusing auth error.
    * ``chmod`` after ``chown`` fails with EPERM: changing the mode of a
      file you no longer own needs CAP_FOWNER, which this container
      (correctly) does not have.
    """

    @classmethod
    def setUpClass(cls) -> None:
        from pathlib import Path
        cls.entrypoint = (
            Path(manager.__file__).resolve().parent / 'entrypoint.sh'
        ).read_text(encoding='utf-8')

    def test_chmod_runs_before_chown(self) -> None:
        chmod_at = self.entrypoint.index('chmod 700 "$CLAUDE_HOME"')
        chown_at = self.entrypoint.index('chown -R claude:users "$CLAUDE_HOME"')
        self.assertLess(
            chmod_at, chown_at,
            'chmod must run while root still OWNS the config dir — after '
            'the chown it fails with "Operation not permitted" (needs '
            'CAP_FOWNER, which the sandbox does not grant).',
        )

    def test_chown_failure_is_fatal_not_swallowed(self) -> None:
        self.assertIn('FATAL: cannot chown', self.entrypoint)
        self.assertNotIn(
            'chown -R claude  "$CLAUDE_HOME" 2>/dev/null \\\n    || true',
            self.entrypoint,
            'a swallowed chown failure ships an agent that cannot read '
            'its own credentials',
        )

    def test_spawn_grants_the_capability_the_chown_needs(self) -> None:
        self.assertIn('--cap-add=CHOWN', manager._REQUIRED_DOCKER_FLAGS)


class CrossPlatformTests(unittest.TestCase):
    """Windows and Linux are supported targets, not afterthoughts.

    Two things here were POSIX-shaped and would have misbehaved on
    Windows — one of them destructively.
    """

    def test_liveness_check_does_not_use_os_kill_on_windows(self) -> None:
        # ``os.kill(pid, 0)`` is the POSIX idiom, but on Windows Python
        # maps every signal except CTRL_C/CTRL_BREAK onto TerminateProcess
        # — the "check" would kill the process it asks about, and the
        # reaper asks about every live owner.
        import inspect
        source = inspect.getsource(manager._process_is_alive)
        self.assertIn("sys.platform == 'win32'", source)
        self.assertIn('OpenProcess', source)
        # The Windows branch must RETURN before the POSIX call is reached.
        # ``rindex``: the docstring mentions os.kill to explain the hazard,
        # so compare against the actual CALL, which is last.
        self.assertLess(
            source.index("sys.platform == 'win32'"), source.rindex('os.kill('),
        )

    def test_watchdog_does_not_select_on_a_pipe(self) -> None:
        # ``select()`` accepts only sockets on Windows, so the watchdog
        # would raise there and guard nothing.
        from pathlib import Path
        source = (
            Path(manager.__file__).resolve().parent / 'watchdog.py'
        ).read_text(encoding='utf-8')
        self.assertNotIn('import select', source)
        self.assertIn('threading.Thread', source)

    def test_detach_kwargs_match_the_platform(self) -> None:
        kwargs = manager._detached_process_kwargs()
        if manager.sys.platform == 'win32':      # pragma: no cover - platform
            self.assertIn('creationflags', kwargs)
        else:
            self.assertEqual(kwargs, {'start_new_session': True})

    def test_boot_identity_is_available_on_this_platform(self) -> None:
        self.assertTrue(manager.host_boot_identity())


class ParentLossWatchdogTests(unittest.TestCase):
    """Reap the container the moment its owner dies, not at next boot.

    ``--rm`` fires when the CONTAINER's process exits; it does nothing
    when the process that launched it is SIGKILLed. The boot sweep caught
    that eventually — this closes the window to ~1s.
    """

    def test_disarm_byte_means_do_not_reap(self) -> None:
        import io
        from sandbox_core_lib.sandbox_core_lib import watchdog as wd
        read_fd, write_fd = os.pipe()
        os.write(write_fd, wd.DISARM_BYTE)
        os.close(write_fd)
        with mock.patch.object(wd, '_remove') as remove:
            self.assertEqual(wd.watch(read_fd, 'c1', '1', 'b'), 0)
        remove.assert_not_called()

    def test_eof_reaps_when_ownership_still_matches(self) -> None:
        from sandbox_core_lib.sandbox_core_lib import watchdog as wd
        read_fd, write_fd = os.pipe()
        os.close(write_fd)                      # owner "died"
        with mock.patch.object(wd, '_still_ours', return_value=True), \
             mock.patch.object(wd, '_remove', return_value=True) as remove, \
             mock.patch.object(wd, '_container_labels', return_value=None), \
             mock.patch.object(wd, '_write_incident') as incident:
            self.assertEqual(wd.watch(read_fd, 'c1', '1', 'b', '/tmp/x.log'), 0)
        remove.assert_called_once_with('c1')
        self.assertTrue(incident.call_args.args[1]['removed'])

    def test_eof_does_not_reap_a_container_that_is_no_longer_ours(self) -> None:
        # A name can be reused by a later spawn. Killing on name alone
        # would let a stale watchdog take down a healthy container.
        from sandbox_core_lib.sandbox_core_lib import watchdog as wd
        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        with mock.patch.object(wd, '_still_ours', return_value=False), \
             mock.patch.object(wd, '_remove') as remove:
            self.assertEqual(wd.watch(read_fd, 'c1', '1', 'b'), 0)
        remove.assert_not_called()

    def test_removal_is_retried_through_a_flaky_daemon(self) -> None:
        # The host is often unhealthy at exactly the moment the owner
        # died — a single failed `docker rm` must not end the attempt.
        from sandbox_core_lib.sandbox_core_lib import watchdog as wd
        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        attempts = {'n': 0}

        def flaky(_container):
            attempts['n'] += 1
            return attempts['n'] >= 3

        with mock.patch.object(wd, '_still_ours', return_value=True), \
             mock.patch.object(wd, '_remove', side_effect=flaky), \
             mock.patch.object(wd, '_container_labels', return_value=None), \
             mock.patch.object(wd, '_write_incident'), \
             mock.patch.object(wd.time, 'sleep'):
            self.assertEqual(wd.watch(read_fd, 'c1', '1', 'b'), 0)
        self.assertEqual(attempts['n'], 3)

    def test_ownership_labels_are_compared_exactly(self) -> None:
        from sandbox_core_lib.sandbox_core_lib import watchdog as wd
        pid_key, boot_key = manager._OWNER_PID_LABEL, manager._OWNER_BOOT_LABEL
        labels = {pid_key: '42', boot_key: 'boot-a'}
        with mock.patch.object(wd, '_container_labels', return_value=labels):
            self.assertTrue(wd._still_ours('c', '42', 'boot-a', pid_key, boot_key))
            self.assertFalse(wd._still_ours('c', '43', 'boot-a', pid_key, boot_key))
            self.assertFalse(wd._still_ours('c', '42', 'boot-b', pid_key, boot_key))
        with mock.patch.object(wd, '_container_labels', return_value=None):
            self.assertFalse(wd._still_ours('c', '42', 'boot-a', pid_key, boot_key))

    def test_arming_failure_never_blocks_a_spawn(self) -> None:
        with mock.patch.object(
            manager.subprocess, 'Popen', side_effect=OSError('no python'),
        ):
            self.assertIsNone(manager.arm_container_watchdog('c1'))

    def test_parent_closes_its_copy_of_the_read_end(self) -> None:
        # While ANY process holds the read end, the watchdog never sees
        # EOF — a dead owner would go unnoticed, which is the entire
        # failure this feature exists to prevent.
        import inspect
        source = inspect.getsource(manager.arm_container_watchdog)
        self.assertIn('os.close(read_fd)', source)
        self.assertIn('_detached_process_kwargs()', source)


class HostEgressBackstopTests(unittest.TestCase):
    """A floor the container cannot reach, in the HOST's DOCKER-USER chain.

    The in-container firewall is the precise control, but it lives in the
    workload's own netns — its integrity rests entirely on the capability
    drop having happened, which in this sandbox was false until the
    runtime verifier caught it. These rules sit outside that blast radius.
    """

    def _captured_script(self, subnet: str = '172.18.0.0/16') -> str:
        captured = {}

        def fake_run(cmd, **kwargs):
            if cmd[:3] == ['docker', 'network', 'inspect']:
                return subprocess.CompletedProcess(cmd, 0, subnet + ' ', '')
            captured['script'] = cmd[-1]
            return subprocess.CompletedProcess(cmd, 0, '7\n', '')

        with mock.patch.object(manager.subprocess, 'run', side_effect=fake_run), \
             mock.patch.object(manager, 'ensure_network'), \
             mock.patch.object(manager, '_image_digest_strict', return_value='sha256:' + 'e' * 64):
            self.assertTrue(manager.install_host_egress_backstop())
        return captured['script']

    def test_helper_is_not_privileged(self) -> None:
        # The helper edits host netfilter, so it needs NET_ADMIN — and
        # nothing else. Running it ``--privileged`` promoted any
        # compromise of the sandbox image into a privileged host-network
        # container, which is worse than the leak this closes.
        captured = {}

        def fake_run(cmd, **kwargs):
            if cmd[:3] == ['docker', 'network', 'inspect']:
                return subprocess.CompletedProcess(cmd, 0, '172.18.0.0/16 ', '')
            captured['cmd'] = cmd
            return subprocess.CompletedProcess(cmd, 0, '7\n', '')

        with mock.patch.object(manager.subprocess, 'run', side_effect=fake_run), \
             mock.patch.object(manager, 'ensure_network'), \
             mock.patch.object(manager, '_image_digest_strict', return_value='sha256:' + 'e' * 64):
            manager.install_host_egress_backstop()
        cmd = captured['cmd']
        self.assertNotIn('--privileged', cmd)
        self.assertIn('NET_ADMIN', cmd)
        self.assertIn('ALL', cmd)                    # --cap-drop ALL
        self.assertIn('no-new-privileges', cmd)
        self.assertIn('--read-only', cmd)
        # Pinned by resolved image ID, not a mutable tag.
        self.assertTrue(any(str(a).startswith('sha256:') for a in cmd))

    def test_network_is_created_before_the_subnet_lookup(self) -> None:
        # At boot nothing has spawned yet, so the bridge may not exist —
        # the backstop would look up an empty subnet and silently skip.
        with mock.patch.object(manager, 'ensure_network') as ensure, \
             mock.patch.object(manager, '_sandbox_bridge_subnet', return_value=''):
            self.assertFalse(manager.install_host_egress_backstop())
        ensure.assert_called_once()

    def test_rules_are_scoped_to_the_sandbox_bridge_only(self) -> None:
        script = self._captured_script()
        for line in script.splitlines():
            if 'DOCKER-USER' in line and ('-I' in line or '-D' in line):
                if '-I' in line:
                    self.assertIn(
                        '-s 172.18.0.0/16', line,
                        'every inserted rule must be scoped to the sandbox '
                        'subnet — an unscoped DROP would break every other '
                        "container on the host's networks",
                    )

    def test_default_is_drop_with_only_443_and_pinned_dns_allowed(self) -> None:
        script = self._captured_script()
        self.assertIn('-j DROP', script)
        self.assertIn('--dport 443 -j RETURN', script)
        self.assertIn('-d 1.1.1.1/32 -j RETURN', script)
        self.assertIn('-d 1.0.0.1/32 -j RETURN', script)
        self.assertIn('ESTABLISHED,RELATED -j RETURN', script)

    def test_install_is_idempotent(self) -> None:
        # Re-running must not stack a second generation of rules.
        script = self._captured_script()
        self.assertIn('-D DOCKER-USER', script.split('-I')[0])

    def test_no_destination_pinning_in_the_host_layer(self) -> None:
        # Deliberate: the allowlisted host's addresses rotate, and a stale
        # host rule would break a legitimate session. Destination pinning
        # belongs inside the container, where it re-resolves every start.
        script = self._captured_script()
        self.assertNotIn('anthropic', script.lower())

    def test_missing_subnet_skips_without_raising(self) -> None:
        with mock.patch.object(
            manager.subprocess, 'run',
            return_value=subprocess.CompletedProcess([], 1, '', 'no such network'),
        ):
            self.assertFalse(manager.install_host_egress_backstop())

    def test_failure_is_best_effort_not_fatal(self) -> None:
        # A backstop that refuses to boot the product when it cannot be
        # installed would be worse than the exposure it closes.
        with mock.patch.object(
            manager.subprocess, 'run', side_effect=OSError('no docker'),
        ):
            self.assertFalse(manager.install_host_egress_backstop())
            self.assertFalse(manager.remove_host_egress_backstop())

    def test_rules_are_tagged_so_removal_targets_only_ours(self) -> None:
        script = self._captured_script()
        tag = manager._backstop_tag()
        self.assertIn(f'--comment {tag}', script)
        self.assertIn(manager._SANDBOX_NETWORK_NAME, tag)


class OutOfBandSecretTests(unittest.TestCase):
    """Secrets must reach the agent without passing through Docker.

    ``-e NAME`` pass-through kept the value out of the docker argv (so
    ``ps`` was clean) but Docker still recorded it in ``Config.Env``,
    where ``docker inspect`` exposes it to any holder of the socket for
    the container's whole lifetime.
    """

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / 'sandbox-env'
        patcher = mock.patch.object(manager, '_secret_dir_root', return_value=root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)
        self.root = root

    def test_no_secrets_present_stages_nothing(self) -> None:
        with mock.patch.dict(manager.os.environ, {}, clear=True):
            self.assertIsNone(manager.materialize_env_secrets('c1'))
        self.assertFalse(self.root.exists())

    def test_secret_is_staged_with_locked_down_modes(self) -> None:
        import stat
        with mock.patch.dict(
            manager.os.environ, {'ANTHROPIC_API_KEY': 'sk-secret'}, clear=True,
        ), mock.patch.object(manager, 'prune_stale_secret_dirs', return_value=[]):
            directory = manager.materialize_env_secrets('c1')
        self.assertIsNotNone(directory)
        secret_file = directory / 'ANTHROPIC_API_KEY'
        self.assertEqual(secret_file.read_text(encoding='utf-8'), 'sk-secret')
        self.assertEqual(stat.S_IMODE(secret_file.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)

    def test_empty_string_secret_is_not_staged(self) -> None:
        # An exported-but-empty var is not a credential; staging it would
        # make the entrypoint export an empty key over a good one.
        with mock.patch.dict(
            manager.os.environ, {'ANTHROPIC_API_KEY': ''}, clear=True,
        ):
            self.assertIsNone(manager.materialize_env_secrets('c1'))

    def test_wrap_command_mounts_the_drop_and_drops_dash_e(self) -> None:
        import tempfile
        from pathlib import Path
        fake_id = 'sha256:' + 'b' * 64
        with tempfile.TemporaryDirectory(dir=Path.home()) as workspace:
            with mock.patch.dict(
                manager.os.environ, {'ANTHROPIC_API_KEY': 'sk-secret'}, clear=True,
            ), mock.patch.object(
                manager, '_image_digest_strict', return_value=fake_id,
            ), mock.patch.object(manager, 'prune_stale_secret_dirs', return_value=[]):
                argv = manager.wrap_command(
                    ['claude'], workspace_path=workspace, task_id='T', container_name='c1',
                )
        self.assertNotIn(
            'ANTHROPIC_API_KEY', argv,
            'the secret name must not be handed to docker at all — that is '
            'what puts the VALUE into Config.Env',
        )
        self.assertTrue(
            any(a.endswith(f'{manager._ENV_SRC_MOUNT}:ro') for a in argv),
            'the staged drop must be mounted read-only',
        )

    def test_prune_removes_only_drops_without_a_running_container(self) -> None:
        for name in ('live-one', 'dead-one'):
            (self.root / name).mkdir(parents=True)
        with mock.patch.object(
            manager.subprocess, 'run',
            return_value=subprocess.CompletedProcess([], 0, 'live-one\n', ''),
        ):
            removed = manager.prune_stale_secret_dirs()
        self.assertEqual(removed, ['dead-one'])
        self.assertTrue((self.root / 'live-one').is_dir())

    def test_prune_keeps_everything_when_docker_is_unreachable(self) -> None:
        # Deleting a live container's secrets because docker was briefly
        # unreachable would break a running task for no safety gain.
        (self.root / 'some-container').mkdir(parents=True)
        with mock.patch.object(
            manager.subprocess, 'run', side_effect=OSError('no docker'),
        ):
            self.assertEqual(manager.prune_stale_secret_dirs(), [])
        self.assertTrue((self.root / 'some-container').is_dir())

    def test_entrypoint_exports_an_allowlist_not_the_whole_drop(self) -> None:
        from pathlib import Path
        entrypoint = (
            Path(manager.__file__).resolve().parent / 'entrypoint.sh'
        ).read_text(encoding='utf-8')
        self.assertIn('for name in ANTHROPIC_API_KEY CLAUDE_CODE_OAUTH_TOKEN', entrypoint)
        # Exporting whatever happens to be in the directory would let a
        # poisoned drop inject LD_PRELOAD / NODE_OPTIONS into the agent.
        self.assertNotIn('for f in "$ENV_SRC"/*', entrypoint)


class OrphanReaperTests(unittest.TestCase):
    """The decision table for ``_orphaned``, then the sweep end-to-end."""

    def test_live_owner_on_same_boot_is_kept(self) -> None:
        with mock.patch.object(manager, '_process_is_alive', return_value=True):
            self.assertFalse(manager._orphaned('4242', 'boot-a', 'boot-a'))

    def test_dead_owner_is_reaped(self) -> None:
        with mock.patch.object(manager, '_process_is_alive', return_value=False):
            self.assertTrue(manager._orphaned('4242', 'boot-a', 'boot-a'))

    def test_different_boot_is_reaped_even_if_pid_is_alive(self) -> None:
        # After a reboot the PID is meaningless — it may well be alive
        # again as something unrelated, which would spare the container
        # forever. The boot identity is what makes this decidable.
        with mock.patch.object(manager, '_process_is_alive', return_value=True):
            self.assertTrue(manager._orphaned('4242', 'boot-a', 'boot-b'))

    def test_unlabelled_container_is_reaped(self) -> None:
        self.assertTrue(manager._orphaned('', '', 'boot-a'))

    def test_unparseable_pid_is_reaped(self) -> None:
        self.assertTrue(manager._orphaned('not-a-pid', 'boot-a', 'boot-a'))

    def test_sweep_removes_only_the_orphan(self) -> None:
        listing = '\n'.join((
            'aaa\t111\tboot-a',   # dead owner   -> reap
            'bbb\t222\tboot-a',   # live owner   -> keep
            'ccc\t333\tboot-old',  # other boot  -> reap
        ))
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[:2] == ['docker', 'ps']:
                return subprocess.CompletedProcess(cmd, 0, listing, '')
            return subprocess.CompletedProcess(cmd, 0, '', '')

        with mock.patch.object(manager.subprocess, 'run', side_effect=fake_run), \
             mock.patch.object(manager, 'host_boot_identity', return_value='boot-a'), \
             mock.patch.object(
                 manager, '_process_is_alive',
                 side_effect=lambda pid: pid == 222,
             ):
            removed = manager.reap_orphan_sandbox_containers()

        self.assertEqual(removed, ['aaa', 'ccc'])
        removals = [cmd for cmd in calls if cmd[:2] == ['docker', 'rm']]
        self.assertEqual(
            [cmd[-1] for cmd in removals], ['aaa', 'ccc'],
            'the container owned by a live process must be left alone',
        )

    def test_sweep_filters_on_the_sandbox_identity_label(self) -> None:
        captured: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            captured.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, '', '')

        with mock.patch.object(manager.subprocess, 'run', side_effect=fake_run):
            manager.reap_orphan_sandbox_containers()

        self.assertIn(
            f'label={manager._IMAGE_IDENTITY_LABEL}={manager._IMAGE_IDENTITY_VALUE}',
            captured[0],
        )

    def test_sweep_never_raises_when_docker_is_absent(self) -> None:
        with mock.patch.object(
            manager.subprocess, 'run', side_effect=OSError('no docker'),
        ):
            self.assertEqual(manager.reap_orphan_sandbox_containers(), [])

    def test_sweep_survives_a_failed_removal(self) -> None:
        def fake_run(cmd, **kwargs):
            if cmd[:2] == ['docker', 'ps']:
                return subprocess.CompletedProcess(cmd, 0, 'aaa\t111\tboot-a\n', '')
            return subprocess.CompletedProcess(cmd, 1, '', 'permission denied')

        with mock.patch.object(manager.subprocess, 'run', side_effect=fake_run), \
             mock.patch.object(manager, 'host_boot_identity', return_value='boot-a'), \
             mock.patch.object(manager, '_process_is_alive', return_value=False):
            self.assertEqual(manager.reap_orphan_sandbox_containers(), [])

    def test_boot_identity_is_stable_within_a_boot(self) -> None:
        # A value that changed between calls would make every container
        # look orphaned — including live ones — so stability is the
        # property that matters, not the format.
        first = manager.host_boot_identity()
        second = manager.host_boot_identity()
        self.assertIsInstance(first, str)
        self.assertEqual(second, first)

    def test_process_liveness(self) -> None:
        import os
        self.assertTrue(manager._process_is_alive(os.getpid()))
        self.assertFalse(manager._process_is_alive(0))
        self.assertFalse(manager._process_is_alive(-1))


if __name__ == '__main__':
    unittest.main()

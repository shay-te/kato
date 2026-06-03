"""Targeted branch coverage for AgentService.

Each test drives one previously-uncovered line / branch in
``kato_core_lib/data_layers/service/agent_service.py`` — almost all of
them error / edge / guard paths. Collaborators are MagicMock /
SimpleNamespace stubs (no network / subprocess / DB). The comment-flow
tests that need on-disk persistence use the real ``WorkspaceService`` +
``LocalCommentStore`` fixtures from ``tests.chaos_lib``.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from kato_core_lib.comment_core_lib import (
    CommentRecord,
    CommentSource,
    CommentStatus,
    KatoCommentStatus,
)
from kato_core_lib.data_layers.service.agent_service import AgentService

from tests.chaos_lib import (
    build_real_agent_service,
    materialize_workspace,
    real_store_for,
)


def _bare_service(**overrides) -> AgentService:
    """AgentService with the 6 required collaborators mocked.

    Optional collaborators (workspace_manager, session_manager, etc.)
    pass through via ``overrides`` so each test wires only what its
    target method reads.
    """
    defaults = dict(
        task_service=MagicMock(),
        task_state_service=MagicMock(),
        implementation_service=MagicMock(),
        testing_service=MagicMock(),
        repository_service=MagicMock(),
        notification_service=MagicMock(),
    )
    defaults.update(overrides)
    return AgentService(**defaults)


# --------------------------------------------------------------------------
# _comment_anchor_is_outdated / _file_line_count  (lines 831, 841, 844-853)
# --------------------------------------------------------------------------


class CommentAnchorOutdatedTests(unittest.TestCase):
    def test_line_anchored_but_missing_repo_id_is_not_outdated(self) -> None:
        # line >= 1 so we pass the first guard, but blank repo_id trips
        # the ``not repo_id or not file_path`` early-return (line 831).
        service = _bare_service()
        record = SimpleNamespace(line=42, repo_id='   ', file_path='a/b.py')
        cache: dict = {}
        self.assertFalse(
            service._comment_anchor_is_outdated('PROJ-1', record, cache)
        )
        self.assertEqual(cache, {})  # never reached the file-count lookup

    def test_line_anchored_missing_file_path_is_not_outdated(self) -> None:
        service = _bare_service()
        record = SimpleNamespace(line=3, repo_id='repo-a', file_path='')
        self.assertFalse(
            service._comment_anchor_is_outdated('PROJ-1', record, {})
        )

    def test_changed_anchor_line_is_outdated(self) -> None:
        service = _bare_service()
        original = service._comment_anchor_line_hash('original line')
        record = SimpleNamespace(
            line=2, repo_id='repo-a', file_path='f.py',
            anchor_line_hash=original,
        )
        cache: dict = {}
        with unittest.mock.patch.object(service, '_file_line_count', return_value=3), \
             unittest.mock.patch.object(service, '_file_line_text',
                                        return_value='changed line'):
            self.assertTrue(
                service._comment_anchor_is_outdated('PROJ-1', record, cache)
            )

    def test_matching_anchor_line_is_not_outdated(self) -> None:
        service = _bare_service()
        original = service._comment_anchor_line_hash('same line')
        record = SimpleNamespace(
            line=2, repo_id='repo-a', file_path='f.py',
            anchor_line_hash=original,
        )
        cache: dict = {}
        with unittest.mock.patch.object(service, '_file_line_count', return_value=3), \
             unittest.mock.patch.object(service, '_file_line_text',
                                        return_value='same line'):
            self.assertFalse(
                service._comment_anchor_is_outdated('PROJ-1', record, cache)
            )


class FileLineCountTests(unittest.TestCase):
    def test_returns_none_without_workspace_manager(self) -> None:
        # line 841 — no workspace manager wired at all.
        service = _bare_service(workspace_manager=None)
        self.assertIsNone(service._file_line_count('PROJ-1', 'repo-a', 'x.py'))

    def test_returns_none_when_repository_path_raises(self) -> None:
        # lines 844-845 — repository_path() blows up -> defensive None.
        wm = SimpleNamespace(
            repository_path=MagicMock(side_effect=RuntimeError('boom')),
        )
        service = _bare_service(workspace_manager=wm)
        self.assertIsNone(service._file_line_count('PROJ-1', 'repo-a', 'x.py'))

    def test_counts_lines_of_real_file(self) -> None:
        # lines 850-851 — the happy path: open + count.
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / 'src').mkdir()
            (repo / 'src' / 'mod.py').write_text('a\nb\nc\n', encoding='utf-8')
            wm = SimpleNamespace(
                repository_path=MagicMock(return_value=repo),
            )
            service = _bare_service(workspace_manager=wm)
            self.assertEqual(
                service._file_line_count('PROJ-1', 'repo-a', 'src/mod.py'), 3
            )

    def test_returns_none_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wm = SimpleNamespace(
                repository_path=MagicMock(return_value=Path(tmp)),
            )
            service = _bare_service(workspace_manager=wm)
            self.assertIsNone(
                service._file_line_count('PROJ-1', 'repo-a', 'nope.py')
            )

    def test_returns_none_when_open_raises(self) -> None:
        # lines 852-853 — is_file() True but open() raises (e.g. perms).
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            target = repo / 'mod.py'
            target.write_text('x\n', encoding='utf-8')
            wm = SimpleNamespace(
                repository_path=MagicMock(return_value=repo),
            )
            service = _bare_service(workspace_manager=wm)
            original_open = Path.open

            def _boom(self, *a, **k):
                if self == target:
                    raise OSError('cannot read')
                return original_open(self, *a, **k)

            with unittest.mock.patch.object(Path, 'open', _boom):
                self.assertIsNone(
                    service._file_line_count('PROJ-1', 'repo-a', 'mod.py')
                )

    def test_reads_specific_file_line_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / 'src').mkdir()
            (repo / 'src' / 'mod.py').write_text(
                'alpha\n\ncharlie\n', encoding='utf-8',
            )
            wm = SimpleNamespace(
                repository_path=MagicMock(return_value=repo),
            )
            service = _bare_service(workspace_manager=wm)
            self.assertEqual(
                service._file_line_text('PROJ-1', 'repo-a', 'src/mod.py', 1),
                'alpha',
            )
            self.assertEqual(
                service._file_line_text('PROJ-1', 'repo-a', 'src/mod.py', 2),
                '',
            )
            self.assertIsNone(
                service._file_line_text('PROJ-1', 'repo-a', 'src/mod.py', 9),
            )

    def test_outdated_uses_cached_line_count(self) -> None:
        # End-to-end: a line past EOF reports outdated; a cached count is
        # reused on the second call (exercises the cache hit).
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / 'f.py').write_text('one\ntwo\n', encoding='utf-8')
            wm = SimpleNamespace(repository_path=MagicMock(return_value=repo))
            service = _bare_service(workspace_manager=wm)
            cache: dict = {}
            rec = SimpleNamespace(line=9, repo_id='repo-a', file_path='f.py')
            self.assertTrue(
                service._comment_anchor_is_outdated('PROJ-1', rec, cache)
            )
            # second call hits the cache, repository_path not called again
            wm.repository_path.reset_mock()
            self.assertTrue(
                service._comment_anchor_is_outdated('PROJ-1', rec, cache)
            )
            wm.repository_path.assert_not_called()


# --------------------------------------------------------------------------
# add_task_comment root-walk: parent vanished mid-walk  (line 913)
# --------------------------------------------------------------------------


class AddTaskCommentBrokenParentChainTests(unittest.TestCase):
    def test_reply_with_dangling_parent_id_breaks_root_walk(self) -> None:
        # A real LocalCommentStore rejects a dangling parent at add()
        # time, so to drive the ``parent is None`` break (line 913) we
        # stub a store whose add() succeeds but get(parent) returns None.
        persisted = SimpleNamespace(
            id='reply-1',
            parent_id='vanished-parent',
            to_dict=lambda: {'id': 'reply-1', 'parent_id': 'vanished-parent'},
        )
        update_status = MagicMock()
        fake_store = SimpleNamespace(
            add=MagicMock(return_value=persisted),
            get=MagicMock(return_value=None),  # parent has vanished
            update_kato_status=update_status,
        )
        service = _bare_service()
        service._comment_store_for = MagicMock(return_value=fake_store)
        service._maybe_trigger_comment_run = MagicMock(return_value=False)

        result = service.add_task_comment(
            'PROJ-1',
            repo_id='repo-a',
            file_path='x.py',
            parent_id='vanished-parent',
            body='another reply',
            author='operator',
        )

        self.assertTrue(result['ok'])
        # The walk broke at the vanished parent, so the persisted reply
        # itself is treated as the thread root that gets requeued.
        self.assertEqual(result['requeued_root_id'], 'reply-1')
        fake_store.get.assert_called_once_with('vanished-parent')
        update_status.assert_called_once()


# --------------------------------------------------------------------------
# complete_in_progress_task_comments chaining failure  (lines 1133-1134)
# --------------------------------------------------------------------------


class CompleteCommentsChainingTests(unittest.TestCase):
    def test_chaining_exception_is_swallowed_and_logged(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            service, workspace = build_real_agent_service(Path(root))
            materialize_workspace(workspace, 'PROJ-1', repository_ids=['repo-a'])
            store = real_store_for(workspace, 'PROJ-1')
            in_prog = store.add(CommentRecord(
                repo_id='repo-a',
                body='work on me',
                author='operator',
                source=CommentSource.LOCAL.value,
                status=CommentStatus.OPEN.value,
                kato_status=KatoCommentStatus.IN_PROGRESS.value,
            ))
            # No session manager -> _task_has_busy_turn is False, so the
            # method proceeds to complete the in-progress comment.
            service.logger = MagicMock()
            # Make the chain step (drain) raise -> lines 1133-1134.
            service.drain_next_queued_task_comment = MagicMock(
                side_effect=RuntimeError('chain blew up')
            )

            completed = service.complete_in_progress_task_comments(
                'PROJ-1', success=False, result_text='',
            )

            self.assertEqual(len(completed), 1)
            self.assertEqual(completed[0]['comment_id'], in_prog.id)
            self.assertEqual(
                completed[0]['kato_status'], KatoCommentStatus.FAILED.value
            )
            service.drain_next_queued_task_comment.assert_called_once_with('PROJ-1')
            service.logger.exception.assert_called()


# --------------------------------------------------------------------------
# advance_finished_comment_runs: get_session raises  (1216 -> 1221 branch)
# --------------------------------------------------------------------------


class AdvanceFinishedRunsSessionLookupTests(unittest.TestCase):
    def test_get_session_exception_leaves_session_none(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            # session manager whose get_session raises; is_alive/stalled
            # paths must treat the session as absent and not advance.
            session_manager = SimpleNamespace(
                get_session=MagicMock(side_effect=RuntimeError('lookup fail')),
            )
            service, workspace = build_real_agent_service(
                Path(root), session_manager=session_manager,
            )
            materialize_workspace(workspace, 'PROJ-1', repository_ids=['repo-a'])
            store = real_store_for(workspace, 'PROJ-1')
            store.add(CommentRecord(
                repo_id='repo-a',
                body='still working',
                author='operator',
                source=CommentSource.LOCAL.value,
                status=CommentStatus.OPEN.value,
                kato_status=KatoCommentStatus.IN_PROGRESS.value,
            ))
            # not stalled, not busy -> reaches the get_session try/except.
            service._task_session_is_stalled = MagicMock(return_value=False)
            service._task_has_busy_turn = MagicMock(return_value=False)

            advanced = service.advance_finished_comment_runs()

            # get_session raised -> session stays None (exception swallowed
            # at 1219-1220), so there's no terminal event and the
            # in-progress comment is requeued as "session gone".
            self.assertEqual(len(advanced), 1)
            self.assertEqual(advanced[0]['task_id'], 'PROJ-1')
            self.assertEqual(advanced[0].get('action'), 'requeued')

    def test_no_session_manager_skips_lookup_block(self) -> None:
        # 1216->1221 false arc: session_manager is None, so the
        # get_session block is skipped entirely and session stays None.
        with tempfile.TemporaryDirectory() as root:
            service, workspace = build_real_agent_service(
                Path(root), session_manager=None,
            )
            materialize_workspace(workspace, 'PROJ-1', repository_ids=['repo-a'])
            store = real_store_for(workspace, 'PROJ-1')
            store.add(CommentRecord(
                repo_id='repo-a',
                body='still working',
                author='operator',
                source=CommentSource.LOCAL.value,
                status=CommentStatus.OPEN.value,
                kato_status=KatoCommentStatus.IN_PROGRESS.value,
            ))
            service._task_session_is_stalled = MagicMock(return_value=False)
            service._task_has_busy_turn = MagicMock(return_value=False)

            advanced = service.advance_finished_comment_runs()

            # no session at all -> requeued as "session gone".
            self.assertEqual(len(advanced), 1)
            self.assertEqual(advanced[0].get('action'), 'requeued')


# --------------------------------------------------------------------------
# sync_pull_request_comments_for_task: run_git callable  (1494 -> 1504)
# --------------------------------------------------------------------------


class SyncRemoteCommentsRunGitTests(unittest.TestCase):
    def test_run_git_pull_invoked_when_callable(self) -> None:
        # Drive the ``if callable(run_git):`` true branch (line 1494) so
        # the git-pull seam fires and control falls through to 1504.
        with tempfile.TemporaryDirectory() as root:
            service, workspace = build_real_agent_service(Path(root))
            materialize_workspace(workspace, 'PROJ-1', repository_ids=['repo-a'])
            # The clone path must contain a .git dir for sync to proceed.
            clone = workspace.repository_path('PROJ-1', 'repo-a')
            (clone / '.git').mkdir(parents=True)

            run_git = MagicMock()
            list_comments = MagicMock(return_value=[])
            service._repository_service = SimpleNamespace(
                get_repository=MagicMock(return_value=SimpleNamespace(id='repo-a')),
                _run_git=run_git,
                list_pull_request_comments=list_comments,
            )
            service.logger = MagicMock()
            # Empty pr_id -> short-circuit after the pull, but we've already
            # exercised lines 1494-1499 and fallen through to 1504.
            service._task_pull_request_id = MagicMock(return_value='')

            result = service.sync_remote_comments('PROJ-1', 'repo-a')

            run_git.assert_called_once()
            args = run_git.call_args[0]
            self.assertEqual(args[1], ['pull', '--ff-only'])
            self.assertTrue(result['ok'])
            self.assertIn('no pull request id', result['note'])

    def test_run_git_not_callable_skips_pull(self) -> None:
        # 1494->1504 false arc: _run_git is absent/not callable, so the
        # pull step is skipped and control falls straight to listing.
        with tempfile.TemporaryDirectory() as root:
            service, workspace = build_real_agent_service(Path(root))
            materialize_workspace(workspace, 'PROJ-1', repository_ids=['repo-a'])
            clone = workspace.repository_path('PROJ-1', 'repo-a')
            (clone / '.git').mkdir(parents=True)

            service._repository_service = SimpleNamespace(
                get_repository=MagicMock(return_value=SimpleNamespace(id='repo-a')),
                _run_git=None,  # not callable -> pull skipped
                list_pull_request_comments=MagicMock(return_value=[]),
            )
            service.logger = MagicMock()
            service._task_pull_request_id = MagicMock(return_value='')

            result = service.sync_remote_comments('PROJ-1', 'repo-a')

            self.assertTrue(result['ok'])
            self.assertTrue(result['pull']['ok'])


# --------------------------------------------------------------------------
# _task_has_in_progress_comment: store.list raises  (lines 1798-1799)
# --------------------------------------------------------------------------


class TaskHasInProgressCommentTests(unittest.TestCase):
    def test_store_list_failure_reports_not_in_progress(self) -> None:
        store = SimpleNamespace(list=MagicMock(side_effect=RuntimeError('io')))
        self.assertFalse(
            AgentService._task_has_in_progress_comment(store, exclude_id='c1')
        )

    def test_other_comment_in_progress_excludes_self(self) -> None:
        c_self = SimpleNamespace(
            id='c1', kato_status=KatoCommentStatus.IN_PROGRESS.value
        )
        c_other = SimpleNamespace(
            id='c2', kato_status=KatoCommentStatus.IN_PROGRESS.value
        )
        store = SimpleNamespace(list=MagicMock(return_value=[c_self, c_other]))
        # excluding c1, c2 is still in progress -> True
        self.assertTrue(
            AgentService._task_has_in_progress_comment(store, exclude_id='c1')
        )
        # excluding c2 too: only the (excluded) c1 remains in progress
        store2 = SimpleNamespace(list=MagicMock(return_value=[c_self]))
        self.assertFalse(
            AgentService._task_has_in_progress_comment(store2, exclude_id='c1')
        )


# --------------------------------------------------------------------------
# _run_comment_agent force_respawn  (lines 1905-1906)
# + _terminate_stalled_session  (1921-1933)
# + _spawn_comment_agent workspace summary branch (1954 -> 1957)
# --------------------------------------------------------------------------


class RunCommentAgentForceRespawnTests(unittest.TestCase):
    def _service_with_live_session(self, **overrides):
        live_session = SimpleNamespace(
            is_alive=True,
            send_user_message=MagicMock(),
        )
        session_manager = SimpleNamespace(
            get_session=MagicMock(return_value=live_session),
            terminate_session=MagicMock(),
        )
        service = _bare_service(session_manager=session_manager, **overrides)
        service._comment_agent_prompt = MagicMock(return_value='do the thing')
        return service, session_manager, live_session

    def test_force_respawn_terminates_then_spawns(self) -> None:
        # lines 1905-1906: force_respawn=True kills the live session and
        # routes to _spawn_comment_agent instead of send_user_message.
        service, session_manager, live = self._service_with_live_session()
        service._spawn_comment_agent = MagicMock(return_value=True)
        record = SimpleNamespace(id='c1')

        result = service._run_comment_agent('PROJ-1', record, force_respawn=True)

        self.assertTrue(result)
        session_manager.terminate_session.assert_called_once_with(
            'PROJ-1', remove_record=False,
        )
        service._spawn_comment_agent.assert_called_once()
        live.send_user_message.assert_not_called()


class TerminateStalledSessionTests(unittest.TestCase):
    def test_no_session_manager_is_noop(self) -> None:
        # line 1921-1922
        service = _bare_service(session_manager=None)
        service._terminate_stalled_session('PROJ-1')  # no raise

    def test_terminate_not_callable_is_noop(self) -> None:
        # lines 1923-1925: terminate_session missing/not callable
        session_manager = SimpleNamespace(terminate_session=None)
        service = _bare_service(session_manager=session_manager)
        service._terminate_stalled_session('PROJ-1')  # no raise

    def test_terminate_called_and_logged(self) -> None:
        # lines 1926-1931 happy path
        terminate = MagicMock()
        session_manager = SimpleNamespace(terminate_session=terminate)
        service = _bare_service(session_manager=session_manager)
        service.logger = MagicMock()
        service._terminate_stalled_session('PROJ-1')
        terminate.assert_called_once_with('PROJ-1', remove_record=False)
        service.logger.info.assert_called_once()

    def test_terminate_exception_swallowed(self) -> None:
        # lines 1932-1933: terminate raises -> logged, not re-raised
        terminate = MagicMock(side_effect=RuntimeError('cannot kill'))
        session_manager = SimpleNamespace(terminate_session=terminate)
        service = _bare_service(session_manager=session_manager)
        service.logger = MagicMock()
        service._terminate_stalled_session('PROJ-1')
        service.logger.exception.assert_called_once()


class SpawnCommentAgentWorkspaceSummaryTests(unittest.TestCase):
    def test_summary_read_from_workspace_manager(self) -> None:
        # line 1954->1957: workspace_manager is not None, so the
        # task_summary is read off the workspace record.
        runner = SimpleNamespace(resume_session_for_chat=MagicMock())
        workspace = SimpleNamespace(task_summary='Fix the login bug')
        wm = SimpleNamespace(get=MagicMock(return_value=workspace))
        service = _bare_service(
            planning_session_runner=runner,
            workspace_manager=wm,
        )
        service.logger = MagicMock()
        service._comment_agent_cwd = MagicMock(return_value='/tmp/ws')
        record = SimpleNamespace(id='c1')

        result = service._spawn_comment_agent('PROJ-1', record, 'prompt text')

        self.assertTrue(result)
        runner.resume_session_for_chat.assert_called_once()
        kwargs = runner.resume_session_for_chat.call_args.kwargs
        self.assertEqual(kwargs['task_id'], 'PROJ-1')
        self.assertEqual(kwargs['message'], 'prompt text')
        wm.get.assert_called_once_with('PROJ-1')

    def test_no_workspace_manager_leaves_summary_blank(self) -> None:
        # 1954->1957 false arc: workspace_manager is None, so the summary
        # lookup is skipped and stays ''.
        runner = SimpleNamespace(resume_session_for_chat=MagicMock())
        service = _bare_service(
            planning_session_runner=runner,
            workspace_manager=None,
        )
        service.logger = MagicMock()
        service._comment_agent_cwd = MagicMock(return_value='')
        record = SimpleNamespace(id='c1')

        result = service._spawn_comment_agent('PROJ-1', record, 'prompt text')

        self.assertTrue(result)
        runner.resume_session_for_chat.assert_called_once()


# --------------------------------------------------------------------------
# _comment_thread_replies  (lines 2032, 2038-2039)
# --------------------------------------------------------------------------


class CommentThreadRepliesTests(unittest.TestCase):
    def test_empty_root_id_returns_empty(self) -> None:
        # line 2032 — root_id falsy after ``str(root_id or '')``.
        service = _bare_service()
        self.assertEqual(service._comment_thread_replies('PROJ-1', ''), [])
        self.assertEqual(service._comment_thread_replies('PROJ-1', None), [])

    def test_store_list_failure_returns_empty(self) -> None:
        # lines 2038-2039: store present but list() raises.
        store = SimpleNamespace(list=MagicMock(side_effect=RuntimeError('io')))
        service = _bare_service()
        service._comment_store_for = MagicMock(return_value=store)
        self.assertEqual(
            service._comment_thread_replies('PROJ-1', 'root-1'), []
        )

    def test_replies_resolve_to_root_through_chain(self) -> None:
        root = SimpleNamespace(id='r', parent_id='', created_at_epoch=1)
        reply1 = SimpleNamespace(id='a', parent_id='r', created_at_epoch=3)
        reply2 = SimpleNamespace(id='b', parent_id='a', created_at_epoch=2)
        store = SimpleNamespace(
            list=MagicMock(return_value=[root, reply1, reply2]),
        )
        service = _bare_service()
        service._comment_store_for = MagicMock(return_value=store)
        replies = service._comment_thread_replies('PROJ-1', 'r')
        # both replies resolve to root r, sorted by created_at_epoch.
        self.assertEqual([c.id for c in replies], ['b', 'a'])


# --------------------------------------------------------------------------
# _task_pull_request_id registry + live fallback (2092->2086, 2124->2120)
# --------------------------------------------------------------------------


class TaskPullRequestIdTests(unittest.TestCase):
    def test_registry_skips_non_matching_then_matches(self) -> None:
        # 2092->2086: first context is wrong repo (loop continues), the
        # second matches and returns its PR id.
        registry = SimpleNamespace(
            list_pull_request_contexts=MagicMock(return_value=[
                {'task_id': 'PROJ-1', 'repository_id': 'other',
                 'pull_request_id': '99'},
                {'task_id': 'PROJ-1', 'repository_id': 'repo-a',
                 'pull_request_id': '42'},
            ]),
        )
        review_service = SimpleNamespace(state_registry=registry)
        service = _bare_service(review_comment_service=review_service)
        self.assertEqual(
            service._task_pull_request_id('PROJ-1', 'repo-a'), '42'
        )

    def test_live_fallback_skips_blank_pr_then_returns(self) -> None:
        # 2124->2120: first PR entry has a blank id (loop continues),
        # second entry yields a real id.
        registry = SimpleNamespace(
            list_pull_request_contexts=MagicMock(return_value=[]),
        )
        review_service = SimpleNamespace(state_registry=registry)
        repository_service = SimpleNamespace(
            get_repository=MagicMock(return_value=SimpleNamespace(id='repo-a')),
            build_branch_name=MagicMock(return_value='feature/proj-1'),
            find_pull_requests=MagicMock(return_value=[
                {'id': '   '},
                {'pull_request_id': '77'},
            ]),
        )
        service = _bare_service(
            repository_service=repository_service,
            review_comment_service=review_service,
        )
        self.assertEqual(
            service._task_pull_request_id('PROJ-1', 'repo-a'), '77'
        )


# --------------------------------------------------------------------------
# sync_task_repositories: existing_task is None  (2555 -> 2564 branch)
# --------------------------------------------------------------------------


class AddTaskRepositoryTagTests(unittest.TestCase):
    def test_no_existing_task_still_adds_tag(self) -> None:
        # 2555->2564: _lookup_task_for_sync returns None so the
        # existing-tags collection loop is skipped, already_tagged is
        # False, and add_tag fires.
        task_service = MagicMock()
        repository_service = SimpleNamespace(
            repositories=[SimpleNamespace(id='repo-b')],
        )
        service = _bare_service(
            task_service=task_service,
            repository_service=repository_service,
        )
        service.logger = MagicMock()
        service._lookup_task_for_sync = MagicMock(return_value=None)
        # Short-circuit the heavy provisioning step after the tag phase.
        service.sync_task_repositories = MagicMock(
            return_value={'synced': []}
        )

        result = service.add_task_repository('PROJ-1', 'repo-b')

        service._lookup_task_for_sync.assert_called_once_with('PROJ-1')
        task_service.add_tag.assert_called_once()
        self.assertTrue(result['tag_added'])


# --------------------------------------------------------------------------
# _sync_requires_session_restart  (2777, 2791->2793, 2795->2793)
# --------------------------------------------------------------------------


class SyncRequiresSessionRestartTests(unittest.TestCase):
    def test_get_session_not_callable_returns_false(self) -> None:
        # line 2777: session manager has a non-callable get_session.
        session_manager = SimpleNamespace(get_session='not-callable')
        service = _bare_service(session_manager=session_manager)
        provisioned = [SimpleNamespace(id='repo-a', local_path='/x')]
        missing = [SimpleNamespace(id='repo-a')]
        self.assertFalse(
            service._sync_requires_session_restart('PROJ-1', provisioned, missing)
        )

    def test_blank_cwd_and_blank_dir_entries_skipped(self) -> None:
        # 2791->2793 (cwd falsy -> skip add) and 2795->2793 (blank
        # raw-dir entry skipped). The new repo path is then outside the
        # (empty) sandbox -> restart required.
        live_session = SimpleNamespace(
            is_alive=True,
            cwd='',  # blank -> cwd branch skipped (2791->2793)
            allowed_additional_dirs=MagicMock(return_value=['', '   ']),
        )
        session_manager = SimpleNamespace(
            get_session=MagicMock(return_value=live_session),
        )
        service = _bare_service(session_manager=session_manager)
        provisioned = [SimpleNamespace(id='repo-a', local_path='/new/repo')]
        missing = [SimpleNamespace(id='repo-a')]
        self.assertTrue(
            service._sync_requires_session_restart('PROJ-1', provisioned, missing)
        )

    def test_path_already_in_sandbox_returns_false(self) -> None:
        live_session = SimpleNamespace(
            is_alive=True,
            cwd='/work',
            allowed_additional_dirs=MagicMock(return_value=['/new/repo']),
        )
        session_manager = SimpleNamespace(
            get_session=MagicMock(return_value=live_session),
        )
        service = _bare_service(session_manager=session_manager)
        provisioned = [SimpleNamespace(id='repo-a', local_path='/new/repo')]
        missing = [SimpleNamespace(id='repo-a')]
        self.assertFalse(
            service._sync_requires_session_restart('PROJ-1', provisioned, missing)
        )


# --------------------------------------------------------------------------
# _lookup_task_for_sync: queue iteration with no match  (2829 -> 2828)
# --------------------------------------------------------------------------


class LookupTaskForSyncTests(unittest.TestCase):
    def test_iterates_past_non_matching_then_matches(self) -> None:
        # 2829->2828: first task in queue does not match (loop body's
        # ``if task_id_matches`` is False), second one matches.
        no_match = SimpleNamespace(id='OTHER-9')
        match = SimpleNamespace(id='PROJ-1')
        task_service = SimpleNamespace(
            get_assigned_tasks=MagicMock(return_value=[no_match, match]),
            get_review_tasks=MagicMock(return_value=[]),
        )
        service = _bare_service(task_service=task_service)
        found = service._lookup_task_for_sync('PROJ-1')
        self.assertIs(found, match)

    def test_no_match_anywhere_returns_none(self) -> None:
        task_service = SimpleNamespace(
            get_assigned_tasks=MagicMock(return_value=[SimpleNamespace(id='A')]),
            get_review_tasks=MagicMock(return_value=[SimpleNamespace(id='B')]),
        )
        service = _bare_service(task_service=task_service)
        self.assertIsNone(service._lookup_task_for_sync('PROJ-1'))


# --------------------------------------------------------------------------
# task_publish_state  (3348->3359, 3373->3346 branches)
# --------------------------------------------------------------------------


class TaskPublishStateTests(unittest.TestCase):
    def _service_with_publish_context(self, repos):
        service = _bare_service()
        task_obj = SimpleNamespace(id='PROJ-1', summary='')
        service._resolve_publish_context = MagicMock(
            return_value=(repos, 'feature/proj-1', task_obj)
        )
        return service

    def test_changes_already_found_skips_second_push_check(self) -> None:
        # 3348->3359: once has_changes_to_push is True from repo #1, the
        # ``if not has_changes_to_push`` guard is False for repo #2, so
        # branch_needs_push is NOT called again.
        repo1 = SimpleNamespace(id='repo-a')
        repo2 = SimpleNamespace(id='repo-b')
        service = self._service_with_publish_context([repo1, repo2])
        branch_needs_push = MagicMock(return_value=True)
        service._repository_service.build_branch_name = MagicMock(
            return_value='feature/proj-1'
        )
        service._repository_service.branch_needs_push = branch_needs_push
        service._repository_service.find_pull_requests = MagicMock(return_value=[])

        result = service.task_publish_state('PROJ-1')

        self.assertTrue(result['has_changes_to_push'])
        # only the first repo triggered branch_needs_push.
        branch_needs_push.assert_called_once()

    def test_pr_without_url_not_appended(self) -> None:
        # 3373->3346: existing PR has no url -> the url-append is skipped
        # but has_pull_request is still True.
        repo1 = SimpleNamespace(id='repo-a')
        service = self._service_with_publish_context([repo1])
        service._repository_service.build_branch_name = MagicMock(
            return_value='feature/proj-1'
        )
        service._repository_service.branch_needs_push = MagicMock(
            return_value=False
        )
        service._repository_service.find_pull_requests = MagicMock(
            return_value=[{'id': '5'}]  # no 'url' key
        )

        result = service.task_publish_state('PROJ-1')

        self.assertTrue(result['has_pull_request'])
        self.assertFalse(result['has_changes_to_push'])
        self.assertEqual(result['pull_request_urls'], [])


if __name__ == '__main__':
    unittest.main()

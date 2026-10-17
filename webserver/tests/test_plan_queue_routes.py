"""HTTP routes for plan queues: every status code, against a real queue.

The real Flask app, a real workspace with a real ``plan.md``, the real queue
service; the agent (proposer) and the host's launcher are the lib's fakes.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from plan_queue_core_lib.plan_queue_core_lib.data.plan import PlanStatus
from plan_queue_core_lib.plan_queue_core_lib.service import PlanQueueService
from plan_queue_core_lib.plan_queue_core_lib.store import PlanQueueStore
from plan_queue_core_lib.plan_queue_core_lib.tests.fakes import (
    FakeLauncher,
    FakeProbe,
    FakeProgress,
    FakeProposer,
    FakeWording,
    split_reply,
    step_spec,
    wait_until,
)
from workspace_core_lib.workspace_core_lib import WorkspaceCoreLib

from kato_webserver.app import create_app
from kato_webserver.plan_queue_routes import _drafts

SPLIT = split_reply([step_spec('api', repositories=['api']), step_spec('ui', depends_on=['api'])])


class PlanQueueRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix='plan-routes-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(os.path.realpath(tmp.name))
        env = patch.dict(os.environ, {
            'KATO_WORKSPACES_ROOT': str(self.root / 'ws'), 'AGENT_WORKSPACES_ROOT': str(self.root / 'ws'),
        })
        env.start()
        self.addCleanup(env.stop)
        self.workspaces = WorkspaceCoreLib(
            root=str(self.root / 'ws'), max_parallel_tasks=1,
            metadata_filename='.kato-meta.json', preflight_log_filename='.kato-preflight.log',
        ).workspaces
        self.workspaces.create(task_id='T-1', task_summary='Discounts', repository_ids=['api', 'web'])
        (self.root / 'ws' / 'T-1' / 'plan.md').write_text('# The plan\nDo it.\n', encoding='utf-8')
        self.refusal = ''
        self.launcher = FakeLauncher()
        self.queue = PlanQueueService(
            store=PlanQueueStore(self.root / 'plans'), proposer=FakeProposer(SPLIT),
            wording=FakeWording(), progress=FakeProgress(), probe=FakeProbe(),
            can_propose=lambda task_id: self.refusal,
        )
        self.queue.bind_launcher(self.launcher)
        self.addCleanup(self.queue.shutdown)
        from claude_core_lib.claude_core_lib.session.manager import ClaudeSessionManager
        manager = ClaudeSessionManager(state_dir=str(self.root / 'sessions'))
        self.addCleanup(manager.shutdown)
        self.app = create_app(
            session_manager=manager, workspace_manager=self.workspaces,
            agent_service=SimpleNamespace(plan_queue=self.queue),
        )
        self.client = self.app.test_client()

    def _proposed(self) -> str:
        response = self.client.post('/api/sessions/T-1/plan-queue', json={'model': 'opus'})
        self.assertEqual(response.status_code, 202, response.get_json())
        plan_id = response.get_json()['plan_id']
        self.assertTrue(wait_until(lambda: self.queue.get(plan_id).status == PlanStatus.PROPOSED))
        return plan_id

    def _post(self, path, body=None):
        return self.client.post(path, json=body or {})

    # ----- the tab list -----

    def test_the_tab_list_carries_the_tasks_live_plan(self) -> None:
        def entry():
            return next(tab for tab in self.client.get('/api/sessions').get_json() if tab['task_id'] == 'T-1')
        self.assertIsNone(entry()['plan_queue'])
        plan_id = self._proposed()
        live = entry()['plan_queue']
        self.assertEqual((live['plan_id'], live['status'], live['step_count']), (plan_id, 'proposed', 2))
        self.client.delete(f'/api/plan-queues/{plan_id}')
        self.assertIsNone(entry()['plan_queue'])  # gone with the plan

    def test_a_queue_that_cannot_answer_leaves_the_tab_list_alone(self) -> None:
        from kato_webserver.app import _plan_queue_summaries
        broken = SimpleNamespace(plan_queue=SimpleNamespace(live_by_source=Mock(side_effect=RuntimeError('x'))))
        self.assertEqual(_plan_queue_summaries(broken), {})
        self.assertEqual(_plan_queue_summaries(SimpleNamespace(plan_queue=None)), {})
        odd = SimpleNamespace(plan_queue=SimpleNamespace(live_by_source=lambda: ['not', 'a', 'map']))
        self.assertEqual(_plan_queue_summaries(odd), {})

    # ----- propose -----

    def test_propose_reads_the_plan_and_the_tasks_repositories(self) -> None:
        plan_id = self._proposed()
        plan = self.client.get(f'/api/plan-queues/{plan_id}').get_json()
        self.assertEqual(plan['source_task_id'], 'T-1')
        self.assertEqual(plan['repository_ids'], ['api', 'web'])
        self.assertEqual(plan['plan_markdown'], '# The plan\nDo it.')
        self.assertEqual((plan['model'], plan['title']), ('opus', 'Discounts'))
        self.assertEqual([step['id'] for step in plan['steps']], ['api', 'ui'])
        summaries = self.client.get('/api/plan-queues').get_json()['plans']
        self.assertEqual([summary['plan_id'] for summary in summaries], [plan_id])

    def test_propose_refusals(self) -> None:
        self.assertEqual(self._post('/api/sessions/T-1/plan-queue', {'model': 'bad model!'}).status_code, 400)
        self.assertEqual(self._post('/api/sessions/T-9/plan-queue').status_code, 400)  # no plan.md
        self.refusal = 'no clone to read'
        refused = self._post('/api/sessions/T-1/plan-queue')
        self.assertEqual((refused.status_code, refused.get_json()['error']), (409, 'no clone to read'))
        self.refusal = ''
        self._proposed()
        self.assertEqual(self._post('/api/sessions/T-1/plan-queue').status_code, 409)  # one at a time

    def test_an_empty_plan_file_is_no_plan(self) -> None:
        (self.root / 'ws' / 'T-1' / 'plan.md').write_text('   ', encoding='utf-8')
        self.assertEqual(self._post('/api/sessions/T-1/plan-queue').status_code, 400)

    def test_unknown_plan_is_404(self) -> None:
        self.assertEqual(self.client.get(f'/api/plan-queues/{"f" * 32}').status_code, 404)

    # ----- revise / edit -----

    def test_revise_and_retry_proposal(self) -> None:
        plan_id = self._proposed()
        self.assertEqual(self._post(f'/api/plan-queues/{plan_id}/revise', {'feedback': ''}).status_code, 400)
        self.assertEqual(self._post(f'/api/plan-queues/{plan_id}/revise', {'feedback': 'smaller'}).status_code, 202)
        self.assertTrue(wait_until(lambda: self.queue.get(plan_id).status == PlanStatus.PROPOSED))
        self.assertEqual(self._post(f'/api/plan-queues/{plan_id}/retry-proposal').status_code, 409)

    def test_edit(self) -> None:
        plan_id = self._proposed()
        good = {'title': 'Renamed', 'steps': [
            {'id': 'one', 'title': 'One', 'brief': 'b', 'depends_on': [], 'repositories': ['api']},
        ]}
        edited = self.client.put(f'/api/plan-queues/{plan_id}/steps', json=good)
        self.assertEqual(edited.status_code, 200)
        self.assertEqual((edited.get_json()['title'], len(edited.get_json()['steps'])), ('Renamed', 1))
        invalid = {'steps': [{'id': 'one', 'title': 'One', 'brief': 'b', 'depends_on': ['ghost']}]}
        response = self.client.put(f'/api/plan-queues/{plan_id}/steps', json=invalid)
        self.assertEqual(response.status_code, 400)
        self.assertIn("unknown step 'ghost'", response.get_json()['error'])
        self.assertEqual(self.client.put(f'/api/plan-queues/{plan_id}/steps', json={'steps': 'x'}).status_code, 400)

    def test_an_edit_may_add_a_repository_for_every_task(self) -> None:
        plan_id = self._proposed()
        steps = self.client.get(f'/api/plan-queues/{plan_id}').get_json()['steps']
        for step in steps:
            step['repositories'] = [*step['repositories'], 'core-lib']
        edited = self.client.put(f'/api/plan-queues/{plan_id}/steps', json={'steps': steps, 'repositories': ['core-lib']})
        self.assertEqual(edited.status_code, 200, edited.get_json())
        self.assertIn('core-lib', edited.get_json()['repository_ids'])
        bad = self.client.put(f'/api/plan-queues/{plan_id}/steps', json={'steps': steps, 'repositories': 'core-lib'})
        self.assertEqual(bad.status_code, 400)

    def test_malformed_edit_bodies(self) -> None:
        self.assertEqual(_drafts(None)[1], '"steps" must be a list')
        self.assertEqual(_drafts(['x'])[1], 'step 1 is not an object')
        self.assertIn('needs text', _drafts([{'id': 'a', 'title': 'T'}])[1])
        self.assertIn('not a list of text', _drafts([{'id': 'a', 'title': 'T', 'brief': 'B', 'depends_on': [1]}])[1])
        drafts, problem = _drafts([{'id': ' a ', 'title': 'T', 'brief': 'B'}])
        self.assertEqual((problem, drafts[0].id, drafts[0].repositories), ('', 'a', ()))

    # ----- approve and control -----

    def test_approve_validation(self) -> None:
        plan_id = self._proposed()
        for body in ({'model': '!!'}, {'start_mode': 'yolo'}, {'effort': 'ultra-mega'}):
            with self.subTest(body=body):
                self.assertEqual(self._post(f'/api/plan-queues/{plan_id}/approve', body).status_code, 400)

    def test_approve_run_pause_stop_discard(self) -> None:
        plan_id = self._proposed()
        approved = self._post(f'/api/plan-queues/{plan_id}/approve', {'start_mode': 'implement', 'run': True})
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.get_json()['status'], 'running')
        self.assertEqual(len(self.launcher.prepared), 2)
        self.assertEqual(self.client.delete(f'/api/plan-queues/{plan_id}').status_code, 409)  # running
        self.assertEqual(self._post(f'/api/plan-queues/{plan_id}/pause').get_json()['status'], 'paused')
        self.assertEqual(self._post(f'/api/plan-queues/{plan_id}/pause').status_code, 409)
        self.assertEqual(self._post(f'/api/plan-queues/{plan_id}/run').get_json()['status'], 'running')
        self.assertEqual(self._post(f'/api/plan-queues/{plan_id}/explode').status_code, 404)
        self.assertEqual(self._post(f'/api/plan-queues/{plan_id}/stop').get_json()['status'], 'cancelled')
        discarded = self.client.delete(f'/api/plan-queues/{plan_id}')
        self.assertEqual((discarded.status_code, discarded.get_json()), (200, {'ok': True}))

    def test_approve_without_a_launcher_is_503(self) -> None:
        plan_id = self._proposed()
        self.queue.bind_launcher(None)
        self.assertEqual(self._post(f'/api/plan-queues/{plan_id}/approve').status_code, 503)

    def test_step_actions(self) -> None:
        plan_id = self._proposed()
        self._post(f'/api/plan-queues/{plan_id}/approve')
        satisfied = self._post(f'/api/plan-queues/{plan_id}/steps/api/satisfy')
        self.assertEqual(satisfied.status_code, 200)
        self.assertEqual(satisfied.get_json()['steps'][0]['status'], 'satisfied')
        self.assertEqual(self._post(f'/api/plan-queues/{plan_id}/steps/ui/retry').status_code, 409)
        self.assertEqual(self._post(f'/api/plan-queues/{plan_id}/steps/ui/check').status_code, 409)
        self.assertEqual(self._post(f'/api/plan-queues/{plan_id}/steps/ui/cancel').status_code, 200)
        self.assertEqual(self._post(f'/api/plan-queues/{plan_id}/steps/ghost/cancel').status_code, 404)
        self.assertEqual(self._post(f'/api/plan-queues/{plan_id}/steps/ui/explode').status_code, 404)

    # ----- a split the operator already wrote as files -----

    def _write_split(self, folder: str, names, overview: str = '') -> Path:
        path = self.root / 'ws' / 'T-1' / folder
        path.mkdir(parents=True, exist_ok=True)
        for name in names:
            (path / name).write_text(f'# {name}\n', encoding='utf-8')
        if overview:
            (path / 'README.md').write_text(overview, encoding='utf-8')
        return path

    def test_split_folders_lists_only_folders_that_hold_a_split(self) -> None:
        self._write_split('tasks', ['00-a.md', '01-b.md', '10-c.md'], overview='# Order')
        self._write_split('memory', ['MEMORY.md', 'notes.md'])            # unnumbered
        self._write_split('repo', ['01-x.md', '02-y.md']).joinpath('.git').mkdir()  # a clone
        self._write_split('.hidden', ['01-x.md', '02-y.md'])
        response = self.client.get('/api/sessions/T-1/plan-queue/split-folders')
        self.assertEqual(response.get_json(), {'folders': [{'path': 'tasks', 'files': 3, 'context': 'README.md'}]})
        self.assertEqual(self.client.get('/api/sessions/T-9/plan-queue/split-folders').get_json(), {'folders': []})

    def test_propose_from_a_split_folder_takes_its_files_as_written(self) -> None:
        (self.root / 'ws' / 'T-1' / 'plan.md').unlink()  # the folder is the split; no plan.md needed
        self._write_split('tasks', ['10-c.md', '00-a.md', '02-b.md', 'notes.md'], overview='# Order\n')
        response = self._post('/api/sessions/T-1/plan-queue', {'split_folder': 'tasks'})
        self.assertEqual(response.status_code, 202, response.get_json())
        plan = response.get_json()
        self.assertEqual([item['path'] for item in plan['split_files']],
                         ['tasks/00-a.md', 'tasks/02-b.md', 'tasks/10-c.md'])
        self.assertEqual(plan['split_files'][0]['text'], '# 00-a.md')
        self.assertEqual(plan['plan_markdown'], '# Order')

    def test_propose_from_a_bad_split_folder_is_refused(self) -> None:
        self._write_split('one', ['01-only.md'])
        for folder, message in (('../ws', 'not a folder of the task'), ('nope', 'holds no split'),
                                ('one', 'holds no split'), ('.hidden', 'not a folder of the task')):
            with self.subTest(folder):
                refused = self._post('/api/sessions/T-1/plan-queue', {'split_folder': folder})
                self.assertEqual(refused.status_code, 400)
                self.assertIn(message, refused.get_json()['error'])
        self.assertEqual(self._post('/api/sessions/T-1/plan-queue', {'split_folder': ['tasks']}).status_code, 400)
        self.assertEqual(self._post('/api/sessions/T-9/plan-queue', {'split_folder': 'tasks'}).status_code, 400)


    # ----- a plan on a shared tracker (the queue's own tests cover what sharing
    # means; these pin the HTTP contract) -----

    def test_a_plan_says_whether_it_could_be_shared_and_sharing_is_a_yes_or_no(self) -> None:
        plan_id = self._proposed()
        self.assertIs(self.client.get(f'/api/plan-queues/{plan_id}').get_json()['can_share'], False)
        self.assertEqual(self._post(f'/api/plan-queues/{plan_id}/approve', {'shared': 'yes'}).status_code, 400)
        refused = self._post(f'/api/plan-queues/{plan_id}/approve', {'shared': True})
        self.assertEqual(refused.status_code, 400)
        self.assertIn('cannot hold its steps as sub-tasks', refused.get_json()['error'])
        kept = self._post(f'/api/plan-queues/{plan_id}/approve', {'shared': False})
        self.assertEqual((kept.status_code, kept.get_json()['mode']), (200, 'local'))
        self.assertEqual(self.client.get(f'/api/plan-queues/{"e" * 32}').status_code, 404)

    def test_take_over_is_only_for_a_plan_another_kato_drives(self) -> None:
        plan_id = self._proposed()
        refused = self._post(f'/api/plan-queues/{plan_id}/take-over')
        self.assertEqual(refused.status_code, 409)
        self.assertIn('not driven by another host', refused.get_json()['error'])

    def test_the_shared_lookup_finds_the_tasks_plan(self) -> None:
        plan_id = self._proposed()
        found = self.client.get('/api/sessions/T-1/plan-queue/shared')
        self.assertEqual((found.status_code, found.get_json()['plan_id']), (200, plan_id))
        missing = self.client.get('/api/sessions/T-9/plan-queue/shared')
        self.assertEqual(missing.status_code, 404)
        self.assertIn('no plan on the tracker includes T-9', missing.get_json()['error'])

    def test_a_sub_task_finds_its_plan_through_its_footer(self) -> None:
        self.workspaces.create(task_id='PROJ-12', task_summary='UI', repository_ids=['web'],
                               task_description=f'Do it.\n\nKato plan queue\nPart of PROJ-1 · task 2 of 2\n'
                                                f'plan-step:{"a" * 32}/ui')
        plan_id = self._proposed()
        followed = []

        def follow_source(source, title=''):
            followed.append((source, title))
            return self.queue.get(plan_id) if source == 'PROJ-1' else None

        self.queue.follow_source = follow_source
        found = self.client.get('/api/sessions/PROJ-12/plan-queue/shared')
        self.assertEqual(found.status_code, 200)
        self.assertEqual(followed, [('PROJ-12', 'UI'), ('PROJ-1', 'UI')])


class NoQueueTests(unittest.TestCase):
    def test_every_route_is_503_before_kato_is_configured(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        from claude_core_lib.claude_core_lib.session.manager import ClaudeSessionManager
        manager = ClaudeSessionManager(state_dir=str(Path(tmp.name) / 'sessions'))
        self.addCleanup(manager.shutdown)
        client = create_app(session_manager=manager, agent_service=None).test_client()
        self.assertEqual(client.get('/api/plan-queues').status_code, 503)
        self.assertEqual(client.post('/api/sessions/T-1/plan-queue', json={}).status_code, 503)
        self.assertEqual(client.post(f'/api/plan-queues/{"a" * 32}/run').status_code, 503)


if __name__ == '__main__':
    unittest.main()

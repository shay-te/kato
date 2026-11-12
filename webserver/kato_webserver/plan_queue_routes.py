"""HTTP routes for plan queues.

    GET    /api/plan-queues                              every plan, summarised
    GET    /api/plan-queues/<plan>                       one plan, every step
    GET    /api/sessions/<task>/plan-queue/split-folders  folders in the task holding a split written as files
    POST   /api/sessions/<task>/plan-queue               split the task's plan.md — or, with ``split_folder``,
                                                         take that folder's split (202 / 400 / 409 / 503)
    POST   /api/plan-queues/<plan>/revise                revise with feedback (202)
    POST   /api/plan-queues/<plan>/retry-proposal        propose again after a failure (202)
    PUT    /api/plan-queues/<plan>/steps                 the operator's edit (200 / 400)
    GET    /api/sessions/<task>/plan-queue/shared         the task's plan as published on the tracker
                                                         (the task is its ticket, or one of its sub-tasks)
    POST   /api/plan-queues/<plan>/approve               approve; ``run`` to start at once; ``shared``
                                                         puts the steps on the tracker as sub-tasks
    POST   /api/plan-queues/<plan>/take-over             drive a plan another kato drove
    POST   /api/plan-queues/<plan>/sync                  reread its steps and driver record now
    POST   /api/plan-queues/<plan>/run | pause | stop
    DELETE /api/plan-queues/<plan>                       forget a plan that is not running
    POST   /api/plan-queues/<plan>/steps/<step>/satisfy | retry | cancel | check

The queue is ``plan_queue_core_lib``; kato's adapters are in
``kato_core_lib.data_layers.service.plan_queue``. A refusal carries the
queue's own reason: 404 unknown, 409 not now, 400 a bad request, 503 the host
can't do it yet.
"""

from __future__ import annotations

from typing import Callable

from flask import Flask, jsonify, request

from kato_core_lib.data_layers.service.local_task_service import START_IMPLEMENT, START_MODES, START_PLAN
from kato_core_lib.data_layers.service.plan_queue.step_footer import parse_footer
from kato_core_lib.data_layers.service.plan_queue.split_folders import (
    SplitFolderError,
    find_split_folders,
    read_split_folder,
)
from kato_core_lib.helpers.done_states_utils import add_done_state, unrecognized_states
from kato_core_lib.helpers.kato_tag_utils import task_branch
from kato_core_lib.helpers.plan_writer import read_task_plan
from kato_core_lib.helpers.workspace_repo_utils import task_workspace_root
from kato_webserver.model_request_utils import requested_model
from plan_queue_core_lib.plan_queue_core_lib.errors import ErrorKind, PlanQueueError
from plan_queue_core_lib.plan_queue_core_lib.graph import StepDraft

_STATUS_BY_KIND = {
    ErrorKind.NOT_FOUND: 404,
    ErrorKind.CONFLICT: 409,
    ErrorKind.INVALID: 400,
    ErrorKind.UNAVAILABLE: 503,
}
_STEP_ACTIONS = ('start', 'plan', 'satisfy', 'retry', 'cancel', 'check', 'delete', 'done')


def register_plan_queue_routes(app: Flask, *, effort_levels: Callable[[], list[str]]) -> None:

    def queue():
        return getattr(app.config.get('AGENT_SERVICE'), 'plan_queue', None)

    def unavailable():
        return jsonify({'error': 'plan queues are not available until kato is configured'}), 503

    def answer(action, status: int = 200):
        """Run ``action`` against the queue; map a refusal to its status."""
        service = queue()
        if service is None:
            return unavailable()
        try:
            result = action(service)
        except PlanQueueError as exc:
            return jsonify({'error': str(exc)}), _STATUS_BY_KIND.get(exc.kind, 409)
        if result is None:
            return jsonify({'ok': True}), status
        return jsonify(with_board_facts(result)), status

    def with_board_facts(plan) -> dict:
        """The plan as the board shows it: ``unknown_states`` — tracker
        states its started tasks sit in that this kato's workflow does not
        name, so a card stuck in one can say why (and offer to count it as
        done)."""
        payload = plan.to_dict()
        if 'steps' not in payload:
            return payload
        payload['merge_branch'] = main_task_branch(plan)
        payload['unknown_states'] = []
        task_states = getattr(app.config.get('AGENT_SERVICE'), 'task_states', None)
        if getattr(plan, 'mode', '') == 'local' or task_states is None:
            return payload
        try:
            known = task_states.known_state_names()
        except Exception:
            return payload
        payload['unknown_states'] = unrecognized_states(
            (step.task_state for step in plan.steps if step.status.is_released), known,
        )
        return payload

    def main_task_branch(plan) -> str:
        """The branch the plan's sub-tasks merge into: the main task's own
        (``task_branch`` — a ``kato:branch:`` tag in its workspace, else its
        id). The board only ever named the task id, so a task created on a
        branch of its own looked as if its plan merged somewhere else."""
        source = str(getattr(plan, 'source_task_id', '') or '')
        workspace_manager = app.config.get('WORKSPACE_MANAGER')
        record = None
        if workspace_manager is not None and source:
            try:
                record = workspace_manager.get(source)
            except Exception:
                record = None
        return task_branch(source, getattr(record, 'tags', None)) if source else ''

    @app.get('/api/plan-queues')
    def list_plan_queues():
        service = queue()
        if service is None:
            return unavailable()
        return jsonify({'plans': service.summaries()})

    @app.get('/api/plan-queues/<plan_id>')
    def get_plan_queue(plan_id: str):
        service = queue()
        if service is None:
            return unavailable()
        try:
            payload = with_board_facts(service.get(plan_id))
            payload['can_share'] = service.can_share(plan_id)
        except PlanQueueError as exc:
            return jsonify({'error': str(exc)}), _STATUS_BY_KIND.get(exc.kind, 409)
        return jsonify(payload)

    @app.post('/api/plan-queues/done-states')
    def add_plan_done_state():
        """Count a tracker state as done from now on (Settings → General →
        States that mean done): a board stuck on a ticket in "Waiting Deploy"
        moves on its next tick, no restart."""
        body = request.get_json(silent=True) or {}
        try:
            listed = add_done_state(body.get('state') if isinstance(body.get('state'), str) else '')
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        except OSError as exc:
            return jsonify({'error': f'the setting could not be saved: {exc}'}), 500
        return jsonify({'done_states': listed})

    @app.get('/api/sessions/<task_id>/plan-queue/shared')
    def shared_plan_queue(task_id: str):
        """The plan this task belongs to, as published on the tracker — followed
        here (read-only) when this kato did not split it."""
        record = (app.config.get('WORKSPACE_MANAGER').get(task_id)
                  if app.config.get('WORKSPACE_MANAGER') is not None else None)
        footer = parse_footer(getattr(record, 'task_description', ''))
        sources = [task_id] + ([footer.source_task_id] if footer is not None else [])

        def find(service):
            known = service.plan_for_task(task_id) or service.plan_for_source(task_id)
            if known is not None:
                return known
            for source in sources:
                followed = service.follow_source(source, title=str(getattr(record, 'task_summary', '') or ''))
                if followed is not None:
                    return followed
            raise PlanQueueError(f'no plan on the tracker includes {task_id}', ErrorKind.NOT_FOUND)

        return answer(find)

    @app.get('/api/sessions/<task_id>/plan-queue/split-folders')
    def plan_queue_split_folders(task_id: str):
        root = task_workspace_root(app.config.get('WORKSPACE_MANAGER'), task_id)
        folders = find_split_folders(root) if root else []
        return jsonify({'folders': [folder.to_dict() for folder in folders]})

    @app.post('/api/sessions/<task_id>/plan-queue')
    def propose_plan_queue(task_id: str):
        service = queue()
        if service is None:
            return unavailable()
        body = request.get_json(silent=True) or {}
        model, problem = requested_model(body)
        if problem:
            return jsonify({'error': problem}), 400
        workspace_manager = app.config.get('WORKSPACE_MANAGER')
        record = workspace_manager.get(task_id) if workspace_manager is not None else None
        split_folder = body.get('split_folder')
        split_files = []
        if split_folder:
            # The operator's own split: its files become the steps as written;
            # its overview (if any) is the "plan" the proposer reads for order.
            root = task_workspace_root(workspace_manager, task_id)
            if not root or not isinstance(split_folder, str):
                return jsonify({'error': f'{task_id} has no folder to take a split from'}), 400
            try:
                split_files, plan_text = read_split_folder(root, split_folder)
            except SplitFolderError as exc:
                return jsonify({'error': str(exc)}), 400
        else:
            plan_text = read_task_plan(workspace_manager, task_id)
            if not plan_text:
                return jsonify({'error': f'{task_id} has no plan to split yet'}), 400
        return answer(lambda svc: svc.propose(
            task_id,
            plan_markdown=plan_text,
            repository_ids=list(getattr(record, 'repository_ids', []) or []),
            source_summary=str(getattr(record, 'task_summary', '') or ''),
            model=model,
            split_files=split_files,
        ), 202)

    @app.post('/api/plan-queues/<plan_id>/revise')
    def revise_plan_queue(plan_id: str):
        body = request.get_json(silent=True) or {}
        return answer(lambda service: service.revise(plan_id, str(body.get('feedback') or '')), 202)

    @app.post('/api/plan-queues/<plan_id>/retry-proposal')
    def retry_plan_queue_proposal(plan_id: str):
        return answer(lambda service: service.retry_proposal(plan_id), 202)

    @app.put('/api/plan-queues/<plan_id>/steps')
    def edit_plan_queue(plan_id: str):
        body = request.get_json(silent=True) or {}
        drafts, problem = _drafts(body.get('steps'))
        if problem:
            return jsonify({'error': problem}), 400
        title = body.get('title') if isinstance(body.get('title'), str) else None
        # Repositories the operator added for the plan's tasks (from kato's inventory).
        repositories = body.get('repositories') or []
        if not isinstance(repositories, list) or not all(isinstance(repo, str) for repo in repositories):
            return jsonify({'error': '"repositories" must be a list of repository names'}), 400
        # The model and effort the plan's tasks run on — only when sent ('' =
        # the configured default): an edit that leaves them out keeps them.
        step_model = effort = None
        if 'model' in body:
            step_model, problem = requested_model(body)
            if problem:
                return jsonify({'error': problem}), 400
        if 'effort' in body:
            effort = str(body.get('effort') or '').strip().lower()
            if effort and effort not in effort_levels():
                return jsonify({'error': f'unknown effort {effort!r}'}), 400
        return answer(lambda service: service.edit(
            plan_id, drafts, title=title, repositories=repositories, step_model=step_model, effort=effort,
        ))

    @app.post('/api/plan-queues/<plan_id>/approve')
    def approve_plan_queue(plan_id: str):
        body = request.get_json(silent=True) or {}
        model, problem = requested_model(body)
        if problem:
            return jsonify({'error': problem}), 400
        start_mode = str(body.get('start_mode') or '').strip()
        if start_mode and start_mode not in START_MODES:
            return jsonify({'error': f'unknown start mode {start_mode!r}', 'allowed': list(START_MODES)}), 400
        effort = str(body.get('effort') or '').strip().lower()
        if effort and effort not in effort_levels():
            return jsonify({'error': f'unknown effort {effort!r}'}), 400
        shared = body.get('shared')
        if shared is not None and not isinstance(shared, bool):
            return jsonify({'error': '"shared" must be true or false'}), 400
        return answer(lambda service: service.approve(
            plan_id, start_mode=start_mode, model=model, effort=effort, run=body.get('run') is True,
            shared=shared,
        ))

    @app.post('/api/plan-queues/<plan_id>/take-over')
    def take_over_plan_queue(plan_id: str):
        return answer(lambda service: service.take_over(plan_id))

    @app.post('/api/plan-queues/<plan_id>/sync')
    def sync_plan_queue(plan_id: str):
        return answer(lambda service: service.sync_now(plan_id))

    @app.post('/api/plan-queues/<plan_id>/<action>')
    def control_plan_queue(plan_id: str, action: str):
        actions = {
            'run': lambda service: service.run(plan_id),
            'pause': lambda service: service.pause(plan_id),
            'stop': lambda service: service.stop(plan_id),
        }
        if action not in actions:
            return jsonify({'error': f'unknown action {action!r}'}), 404
        return answer(actions[action])

    @app.delete('/api/plan-queues/<plan_id>')
    def discard_plan_queue(plan_id: str):
        return answer(lambda service: service.discard(plan_id))

    def delete_step(plan_id: str, step_id: str):
        """Delete a sub-task: its open pull requests declined, then the step
        out of the plan (``plan_queue/step_deletion.py``). The board then
        deletes the task itself (its folder, chat, containers) — the tab's ✕."""
        from kato_core_lib.data_layers.service.plan_queue.step_deletion import PlanStepDeletion

        def run(service):
            agent = app.config.get('AGENT_SERVICE')
            deletion = PlanStepDeletion(queue=service, repository_service=getattr(agent, 'repository_service', None))
            return deletion.delete(plan_id, step_id)

        service = queue()
        if service is None:
            return unavailable()
        try:
            outcome = run(service)
        except PlanQueueError as exc:
            return jsonify({'error': str(exc)}), _STATUS_BY_KIND.get(exc.kind, 409)
        body, status = _deleted_answer(outcome)
        return jsonify(body), status

    def move_step_task_to_done(service, plan_id: str, step_id: str):
        """A started card's Done: its ticket moves to Done, and nothing else —
        no push, no pull request (the operator: "just move the task to done,
        don't try to create PRs"). The board is not the task's own page: it is
        often another kato's task (assigned to someone else), with no clone
        here to push. ``mark_task_done`` is ``move_task_to_done``, so every
        "on done" observer hears it (release notes, Slack, the board). The tasks
        after it still wait for its pull request to be merged — the merge check
        is what keeps them from starting without its code."""
        step = service.get(plan_id).step(step_id)
        if step is None:
            raise PlanQueueError(f'the plan has no step {step_id}', ErrorKind.NOT_FOUND)
        if not step.task_id or step.prepared_only:
            # A waiting card's way to done is "Count as done", which also frees
            # what waits on it — never a quiet ticket move from here.
            raise PlanQueueError(f'step {step_id} has not started — start it, or Count it as done')
        mark_done = getattr(app.config.get('AGENT_SERVICE'), 'mark_task_done', None)
        if not callable(mark_done):
            raise PlanQueueError('kato cannot move tasks right now', ErrorKind.UNAVAILABLE)
        try:
            mark_done(step.task_id)
        except Exception as exc:
            raise PlanQueueError(
                f'could not move {step.task_id} to Done: {exc}', ErrorKind.UNAVAILABLE,
            ) from exc
        return service.get(plan_id)

    @app.post('/api/plan-queues/<plan_id>/steps/<step_id>/<action>')
    def plan_queue_step_action(plan_id: str, step_id: str, action: str):
        if action not in _STEP_ACTIONS:
            return jsonify({'error': f'unknown action {action!r}'}), 404
        if action == 'delete':
            return delete_step(plan_id, step_id)
        if action == 'done':
            return answer(lambda service: move_step_task_to_done(service, plan_id, step_id))
        # A ready card's two ways to start ONE task: Plan (its agent plans
        # first, in Plan mode) or Start (it implements right away) — the
        # operator's pick for that task, whatever was chosen at Approve.
        starts = {'start': START_IMPLEMENT, 'plan': START_PLAN}
        if action in starts:
            return answer(lambda service: service.start_step(plan_id, step_id, start_mode=starts[action]))
        methods = {
            'satisfy': 'mark_satisfied',
            'retry': 'retry_step',
            'cancel': 'cancel_step',
            'check': 'check_now',
        }
        return answer(lambda service: getattr(service, methods[action])(plan_id, step_id))


def _deleted_answer(outcome: dict) -> tuple[dict, int]:
    """The board's answer to a delete: the plan (when the step left it) and
    what happened — its task, the pull requests declined, what stopped it."""
    deleted = {key: outcome[key] for key in ('task_id', 'declined', 'problems')}
    if outcome['plan'] is None:
        return {'error': 'the task was not deleted: ' + '; '.join(outcome['problems']), 'deleted': deleted}, 502
    return {**outcome['plan'].to_dict(), 'deleted': deleted}, 200


def _drafts(value) -> tuple[list[StepDraft], str]:
    """The edit's steps as drafts, or a reason the body is malformed."""
    if not isinstance(value, list):
        return [], '"steps" must be a list'
    drafts = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            return [], f'step {index} is not an object'
        texts = {key: item.get(key) for key in ('id', 'title', 'brief')}
        if not all(isinstance(text, str) for text in texts.values()):
            return [], f'step {index} needs text "id", "title" and "brief"'
        lists = {key: item.get(key) or [] for key in ('depends_on', 'repositories')}
        if not all(isinstance(entries, list) and all(isinstance(entry, str) for entry in entries)
                   for entries in lists.values()):
            return [], f'step {index} has a "depends_on" or "repositories" that is not a list of text'
        # The model this one task runs on ('' = the plan's).
        model, problem = requested_model(item)
        if problem:
            return [], f'step {index}: {problem}'
        drafts.append(StepDraft(
            id=texts['id'].strip(), title=texts['title'], brief=texts['brief'],
            depends_on=tuple(lists['depends_on']), repositories=tuple(lists['repositories']),
            model=model,
        ))
    return drafts, ''

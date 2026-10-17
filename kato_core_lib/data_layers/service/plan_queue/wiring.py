"""Build the process's one plan queue service, and its boot / shutdown hooks.

Where an approved plan's steps live is decided per plan (``plan_mode``): real
sub-tasks on the tracker when it can hold them and the plan's ticket is a
tracker ticket, otherwise kato's own local tasks. The tracker launcher is bound
here; the local one later, after boot, by the webserver that starts local
tasks — until then no local step is started.
"""

from __future__ import annotations

from pathlib import Path

from plan_queue_core_lib.plan_queue_core_lib.data.plan import Plan, PlanMode
from plan_queue_core_lib.plan_queue_core_lib.service import PlanQueueService
from plan_queue_core_lib.plan_queue_core_lib.store import PlanQueueStore

from kato_core_lib.data_layers.service.plan_queue.events import log_plan_event
from kato_core_lib.data_layers.service.chat_question import ChatQuestion
from kato_core_lib.data_layers.service.plan_queue.history_probe import GitHistoryProbe
from kato_core_lib.data_layers.service.plan_queue.local_progress import LocalTaskProgress
from kato_core_lib.data_layers.service.plan_queue.ownership import KatoOwnership
from kato_core_lib.data_layers.service.plan_queue.parent_question import ParentQuestions
from kato_core_lib.data_layers.service.plan_queue.plan_record_on_tracker import (
    TrackerRecordPublisher,
    TrackerRecordSource,
)
from kato_core_lib.data_layers.service.plan_queue.pull_request_probe import KatoPullRequestProbe
from kato_core_lib.data_layers.service.plan_queue.split_in_chat import (
    ChatSplitConversation,
    ChatSplitProposer,
)
from kato_core_lib.data_layers.service.plan_queue.split_proposer import (
    split_refusal,
    split_timeout_seconds,
)
from kato_core_lib.data_layers.service.review_loop_adapters import KatoChatChannel
from kato_core_lib.data_layers.service.plan_queue.split_wording import KatoSplitWording
from kato_core_lib.data_layers.service.plan_queue.tracker_launcher import TrackerStepLauncher
from kato_core_lib.data_layers.service.plan_queue.tracker_progress import (
    RoutedTaskProgress,
    TrackerTaskProgress,
)
from kato_core_lib.helpers.kato_paths_utils import kato_home_path
from kato_core_lib.helpers.late_binding import provider_for

PLAN_QUEUES_DIR_ENV = 'KATO_PLAN_QUEUES_DIR'
_RESTART_REASON = 'kato restarted — press Run to carry on'
_PROPOSAL_RESTART_REASON = 'kato restarted while the split was being worked out — try again, or start over'


def plan_queues_root() -> Path:
    """``~/.kato/plan_queues`` — outside every repository clone."""
    return kato_home_path('plan_queues', env_key=PLAN_QUEUES_DIR_ENV)


def plan_mode(plan: Plan, *, task_service, local_task_store) -> PlanMode:
    """Tracker sub-tasks when the tracker can hold them and the plan was split
    from a tracker ticket; kato's own local tasks otherwise."""
    if local_task_store.owns(plan.source_task_id):
        return PlanMode.LOCAL
    return PlanMode.TRACKER if task_service.subtask_tracker() is not None else PlanMode.LOCAL


def build_plan_queue_service(
    *,
    workspace_manager,
    repository_service,
    local_task_store,
    logger,
    comment_runs=None,
    session_manager=None,
    task_service=None,
    task_state_service=None,
) -> PlanQueueService:
    """Collaborators may be ``later(...)`` markers, resolved per call."""
    get_logger = provider_for(logger)
    get_workspace_manager = provider_for(workspace_manager)
    # The split is proposed in the task's chat — the review loop's channel,
    # minus its Plan-mode refusal (a split is only read and answered).
    chat = KatoChatChannel(
        comment_runs=comment_runs, session_manager=session_manager,
        workspace_manager=workspace_manager, mode_refusal=lambda task_id: '', label='plan split',
    )
    get_task_service = provider_for(task_service)
    local_progress = LocalTaskProgress(local_task_store=local_task_store, workspace_manager=workspace_manager)
    shared = {}
    if task_service is not None and task_state_service is not None:
        ownership = KatoOwnership(task_service=task_service, root=plan_queues_root())
        shared = {
            'mode_for': lambda plan: plan_mode(
                plan, task_service=get_task_service(), local_task_store=local_task_store,
            ),
            'ownership': ownership,
            'publisher': TrackerRecordPublisher(task_service=task_service, ownership=ownership),
            'record_source': TrackerRecordSource(task_service=task_service, task_state_service=task_state_service),
            'history': GitHistoryProbe(repository_service=repository_service),
        }
        progress = RoutedTaskProgress(
            local=local_progress,
            tracker=TrackerTaskProgress(
                task_service=task_service, task_state_service=task_state_service,
                workspace_manager=workspace_manager,
            ),
            is_local=local_task_store.owns,
        )
    else:
        progress = local_progress
    service = PlanQueueService(
        store=PlanQueueStore(plan_queues_root(), logger=get_logger()),
        proposer=ChatSplitProposer(chat=chat, timeout_seconds=split_timeout_seconds()),
        conversation=ChatSplitConversation(chat=chat),
        wording=KatoSplitWording(),
        progress=progress,
        probe=KatoPullRequestProbe(repository_service=repository_service),
        observer=lambda plan, event: log_plan_event(get_logger(), plan, event),
        can_propose=lambda task_id: split_refusal(task_id, workspace_manager=get_workspace_manager()),
        logger=get_logger(),
        **shared,
    )
    if shared:
        service.bind_launcher(
            TrackerStepLauncher(
                task_service=task_service, task_state_service=task_state_service,
                repository_service=repository_service, ownership=shared['ownership'],
            ),
            mode=PlanMode.TRACKER,
        )
    return service


def build_parent_questions(*, queue, comment_runs=None, session_manager=None, workspace_manager=None) -> ParentQuestions:
    """A plan sub-task's question, put to the chat its plan was split in —
    through the same channel as the split (Plan mode suits it: it is only
    read and answered), with the same wait for the chat to be free."""
    chat = KatoChatChannel(
        comment_runs=comment_runs, session_manager=session_manager,
        workspace_manager=workspace_manager, mode_refusal=lambda task_id: '', label='plan question',
    )
    return ParentQuestions(
        queue=queue, question=ChatQuestion(chat=chat, timeout_seconds=split_timeout_seconds()),
    )


def mark_interrupted_plan_queues(service: PlanQueueService) -> None:
    """Boot: nothing that was in motion carries on unasked."""
    service.mark_interrupted(_RESTART_REASON, proposal_reason=_PROPOSAL_RESTART_REASON)


def shut_down_plan_queues(service: PlanQueueService) -> None:
    service.shutdown()

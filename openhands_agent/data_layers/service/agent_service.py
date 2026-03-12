import logging
import traceback

from openhands_agent.data_layers.data.review_comment import ReviewComment
from openhands_agent.data_layers.data.task import Task
from openhands_agent.data_layers.data_access.pull_request_data_access import PullRequestDataAccess
from openhands_agent.data_layers.data_access.task_data_access import TaskDataAccess
from openhands_agent.fields import (
    ImplementationFields,
    PullRequestFields,
    StatusFields,
)
from openhands_agent.data_layers.service.implementation_service import ImplementationService
from openhands_agent.data_layers.service.notification_service import NotificationService

class AgentService:
    def __init__(
        self,
        task_data_access: TaskDataAccess,
        implementation_service: ImplementationService,
        pull_request_data_access: PullRequestDataAccess,
        notification_service: NotificationService,
    ) -> None:
        if notification_service is None:
            raise ValueError('notification_service is required')
        self._task_data_access = task_data_access
        self._implementation_service = implementation_service
        self._pull_request_data_access = pull_request_data_access
        self._notification_service = notification_service
        self._pull_request_branch_map: dict[str, str] = {}
        self.logger = logging.getLogger(self.__class__.__name__)

    @property
    def notification_service(self) -> NotificationService:
        return self._notification_service

    def validate_connections(self) -> None:
        validations = [
            ('youtrack', self._task_data_access.validate_connection),
            ('openhands', self._implementation_service.validate_connection),
            (
                self._pull_request_data_access.provider_name,
                self._pull_request_data_access.validate_connection,
            ),
        ]
        failures: list[str] = []

        for service_name, validate in validations:
            try:
                validate()
                self.logger.info('validated %s connection', service_name)
            except Exception:
                self.logger.exception('failed to validate %s connection', service_name)
                failures.append(
                    f'[{service_name}]\n{traceback.format_exc().rstrip()}'
                )

        if failures:
            raise RuntimeError(
                'startup dependency validation failed:\n\n' + '\n\n'.join(failures)
            )

    def process_assigned_tasks(self) -> list[dict[str, str]]:
        results: list[dict[str, str]] = []

        for task in self._task_data_access.get_assigned_tasks():
            self.logger.info('processing task %s', task.id)
            execution = self._implementation_service.implement_task(task) or {}
            if not self._implementation_succeeded(execution):
                self.logger.warning('implementation failed for task %s', task.id)
                continue

            branch_name = self._resolved_branch_name(task, execution)
            pr = self._create_pull_request(task, branch_name, execution)
            self._remember_pull_request_branch(pr, branch_name)
            self.logger.info(
                'created pull request %s for task %s',
                pr[PullRequestFields.ID],
                task.id,
            )
            self._task_data_access.add_pull_request_comment(
                task.id,
                pr[PullRequestFields.URL],
            )
            self._task_data_access.move_task_to_review(task.id)
            self._notify_task_ready_for_review(task, pr)
            results.append(pr)

        return results

    def handle_pull_request_comment(self, payload: dict) -> dict[str, str]:
        comment = self._implementation_service.review_comment_from_payload(payload)
        self.logger.info(
            'processing review comment %s for pull request %s',
            comment.comment_id,
            comment.pull_request_id,
        )
        branch_name = self._pull_request_branch_map.get(comment.pull_request_id)
        if not branch_name:
            raise ValueError(f'unknown pull request id: {comment.pull_request_id}')

        execution = self._implementation_service.fix_review_comment(comment, branch_name) or {}
        if not execution.get(ImplementationFields.SUCCESS, False):
            raise RuntimeError(f'failed to address comment {comment.comment_id}')

        return {
            StatusFields.STATUS: StatusFields.UPDATED,
            ReviewComment.pull_request_id.key: comment.pull_request_id,
            Task.branch_name.key: branch_name,
        }

    @staticmethod
    def _implementation_succeeded(execution: dict[str, str | bool]) -> bool:
        return bool(execution.get(ImplementationFields.SUCCESS, False))

    @staticmethod
    def _resolved_branch_name(task: Task, execution: dict[str, str | bool]) -> str:
        return str(execution.get(Task.branch_name.key) or task.branch_name)

    def _create_pull_request(
        self,
        task: Task,
        branch_name: str,
        execution: dict[str, str | bool],
    ) -> dict[str, str]:
        return self._pull_request_data_access.create_pull_request(
            title=f'{task.id}: {task.summary}',
            source_branch=branch_name,
            description=str(execution.get(Task.summary.key) or ''),
        )

    def _remember_pull_request_branch(
        self,
        pull_request: dict[str, str],
        branch_name: str,
    ) -> None:
        self._pull_request_branch_map[pull_request[PullRequestFields.ID]] = branch_name

    def _notify_task_ready_for_review(self, task: Task, pull_request: dict[str, str]) -> None:
        try:
            self._notification_service.notify_task_ready_for_review(task, pull_request)
        except Exception:
            self.logger.exception('failed to send completion notification for task %s', task.id)

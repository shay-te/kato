from __future__ import annotations

from core_lib.jobs.job import Job

from kato_core_lib.data_layers.data.fields import StatusFields
from kato_core_lib.helpers.error_handling_utils import log_and_notify_failure
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.kato_core_lib import KatoCoreLib


def collect_processing_results(service) -> list[dict]:
    """Run a scan cycle.

    Tasks fan out across the parallel runner (when wired) so multiple
    tasks run concurrently up to ``KATO_MAX_PARALLEL_TASKS``. The scan
    loop itself stays single-threaded — it polls the ticket system,
    decides which tasks to start, and submits them. Review comments
    fan out the same way: each comment's review-fix is submitted under
    its task id, so cross-task fixes run concurrently while same-task
    fixes serialize via the runner's per-task dedup lock.
    """
    results = _dispatch_assigned_tasks(service)
    results.extend(_dispatch_review_comments(service))
    return results


def _dispatch_assigned_tasks(service) -> list[dict]:
    """Submit each assigned task; collect results from already-finished workers."""
    runner = getattr(service, 'parallel_task_runner', None)
    assigned_tasks = service.get_assigned_tasks()
    if not _runner_has_real_concurrency(runner):
        # Legacy / single-worker path: run inline so the scan loop blocks
        # until each task is fully processed (preserves the original
        # behavior for setups with KATO_MAX_PARALLEL_TASKS=1, and keeps
        # mocked test setups using sync semantics).
        return _process_inline(service, assigned_tasks)
    # Submit-then-don't-block: a future-completed task's result lands in
    # ``results``; everything else continues running until the next scan.
    submitted_futures = []
    for task in assigned_tasks:
        if runner.is_in_flight(str(task.id)):
            continue
        future = runner.submit(
            str(task.id),
            (lambda t=task: service.process_assigned_task(t)),
        )
        if future is not None:
            submitted_futures.append(future)
    return _drain_finished_futures(submitted_futures)


def _runner_has_real_concurrency(runner) -> bool:
    """True only when ``runner`` is a real ParallelTaskRunner with > 1 worker.

    Guards against test mocks where ``runner.max_workers`` is a Mock
    (truthy, not int-comparable) and against single-worker production
    setups where the inline path is the same effective behavior with
    fewer moving parts.
    """
    if runner is None:
        return False
    max_workers = getattr(runner, 'max_workers', None)
    if not isinstance(max_workers, int):
        return False
    return max_workers > 1


def _process_inline(service, assigned_tasks) -> list[dict]:
    results: list[dict] = []
    for task in assigned_tasks:
        result = service.process_assigned_task(task)
        if result is not None:
            results.append(result)
    return results


def _drain_finished_futures(futures) -> list[dict]:
    """Return results for futures that already completed; let others keep running.

    The scan loop ticks every ~30s, so a long task that's still running
    just gets reported next cycle. Failures bubble out as exceptions
    here so the caller's existing error-handling can log + notify.
    """
    results: list[dict] = []
    for future in futures:
        if not future.done():
            continue
        try:
            result = future.result(timeout=0)
        except Exception:
            # Surface so log_and_notify_failure handles it consistently
            # with the legacy path.
            raise
        if result is not None:
            results.append(result)
    return results


def _process_review_comment_best_effort(service, comment) -> dict | None:
    try:
        return service.process_review_comment(comment)
    except Exception:
        return None


def _dispatch_review_comments(service) -> list[dict]:
    """Submit each review comment to the parallel runner under its task id.

    Without a real parallel runner we fall back to the legacy inline path
    (one comment fully processed before the next) to preserve the
    behaviour single-worker / mocked-test setups depend on.

    With a real runner: each comment is submitted under
    ``task_id_for_review_comment``. The runner's per-task dedup lock
    means two comments on the same task serialize naturally (the second
    submit returns ``None`` and we'll retry on the next scan), while
    comments on different tasks run concurrently.
    """
    runner = getattr(service, 'parallel_task_runner', None)
    comments = service.get_new_pull_request_comments()
    if not _runner_has_real_concurrency(runner):
        results: list[dict] = []
        for comment in comments:
            result = _process_review_comment_best_effort(service, comment)
            if result is not None:
                results.append(result)
        return results
    submitted_futures = []
    for comment in comments:
        task_id = service.task_id_for_review_comment(comment)
        if not task_id:
            # Fall back to inline for comments we can't key — can't safely
            # run them through the runner without a dedup key.
            result = _process_review_comment_best_effort(service, comment)
            if result is not None:
                submitted_futures.append(_completed_future(result))
            continue
        if runner.is_in_flight(task_id):
            continue
        future = runner.submit(
            task_id,
            (lambda c=comment: _process_review_comment_best_effort(service, c)),
        )
        if future is not None:
            submitted_futures.append(future)
    return _drain_finished_futures(submitted_futures)


def _completed_future(value):
    """Wrap an already-computed value in a Future so the drain code stays uniform."""
    from concurrent.futures import Future

    future: Future = Future()
    future.set_result(value)
    return future


class ProcessAssignedTasksJob(Job):
    def __init__(self) -> None:
        self.logger = configure_logger(self.__class__.__name__)

    def initialized(self, data_handler: KatoCoreLib) -> None:
        assert isinstance(data_handler, KatoCoreLib)
        self._data_handler = data_handler

    def run(self) -> None:
        try:
            results = collect_processing_results(self._data_handler.service)
            self._log_scan_results(results)
        except Exception as exc:
            log_and_notify_failure(
                logger=self.logger,
                notification_service=self._data_handler.service.notification_service,
                operation_name='process_assigned_task_job',
                error=exc,
                failure_log_message='process_assigned_tasks_job failed',
                notification_failure_log_message=(
                    'failed to send failure notification for process_assigned_task_job'
                ),
            )
            raise

    def _log_scan_results(self, results: list[dict]) -> None:
        results_to_log = [
            result
            for result in results
            if result.get(StatusFields.STATUS) != StatusFields.SKIPPED
        ]
        if results_to_log:
            self.logger.info(
                'completed processing results:\n%s',
                format_processing_results(results_to_log),
            )


def format_processing_results(results: list[dict]) -> str:
    return '\n'.join(
        f'- {_format_processing_result(result)}'
        for result in results
    )


def _format_processing_result(result: dict) -> str:
    status = str(result.get('status', 'unknown'))
    pull_request_id = result.get('pull_request_id')
    branch_name = result.get('branch_name')
    repository_id = result.get('repository_id')

    details: list[str] = [status]
    if pull_request_id:
        details.append(f'PR #{pull_request_id}')
    if branch_name:
        details.append(f'branch {branch_name}')
    if repository_id:
        details.append(f'repository {repository_id}')

    return ' | '.join(details)

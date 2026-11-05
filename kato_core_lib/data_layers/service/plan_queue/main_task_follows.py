"""A sub-task's pull request merged into the main task's branch: the main
task's own clones pull it, so its Changes show the plan's work as it lands.

The main task's clones are never pushed to by the plan — its sub-tasks' pull
requests merge into its branch on the remote — so without this they sat on the
commit they were cloned at and every repository said "Nothing changed yet".
The pull is the header's Pull button (``publish.pull_task``): a fast-forward,
or — the main task's clone has commits of its own, e.g. a ``Merge master`` —
a merge of its remote branch. Nobody pressed this one, so it is asked with
``keep_conflicts=False``: a merge that conflicts is undone and said so, and a
dirty tree is left alone.
"""

from __future__ import annotations

import threading

from kato_core_lib.helpers.late_binding import provider_for


def _in_background(work) -> None:
    threading.Thread(target=work, name='plan-main-task-pull', daemon=True).start()


class MainTaskFollowsMerges(object):
    def __init__(self, *, pull_task, logger, run_later=_in_background) -> None:
        self._pull_task = pull_task
        self._get_logger = provider_for(logger)
        self._run_later = run_later

    def __call__(self, plan, step) -> None:
        source, merged = plan.source_task_id, step.task_id or step.id
        self._run_later(lambda: self._pull(source, merged))

    def _pull(self, source: str, merged: str) -> None:
        logger = self._get_logger()
        outcome = self._pull_task(source) or {}
        if outcome.get('error'):
            logger.info('%s was merged into %s, but %s was not pulled here: %s',
                        merged, source, source, outcome['error'])
            return
        for repo in outcome.get('pulled_repositories') or []:
            logger.info('%s was merged: %s pulled %s commit(s) in %s',
                        merged, source, repo.get('commits_pulled'), repo.get('repository_id'))
        for repo in outcome.get('skipped_repositories') or []:
            logger.info('%s was merged, but %s did not pull %s: %s',
                        merged, source, repo.get('repository_id'), repo.get('reason') or repo.get('detail'))
        for repo in outcome.get('failed_repositories') or []:
            logger.warning('%s was merged, but %s could not pull %s: %s',
                           merged, source, repo.get('repository_id'), repo.get('error'))

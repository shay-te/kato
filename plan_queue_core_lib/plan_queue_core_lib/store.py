"""Where plans live on disk: ``<root>/<plan_id>.json``, one file per plan.

The root is the host's choice and should sit outside any repository a task
works in. File names come only from validated plan ids, so an id from a
request can never point outside the root. A file that can't be read is skipped
(and left untouched for an operator to inspect), never overwritten blind.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from utils_core_lib.utils_core_lib.atomic_write import atomic_write_json

from plan_queue_core_lib.plan_queue_core_lib.data.plan import Plan

_PLAN_ID = re.compile(r'^[0-9a-f]{32}$')
_SUFFIX = '.json'
_REPLY_SUFFIX = '.reply.txt'


class PlanQueueStore(object):
    def __init__(self, root, *, logger: logging.Logger | None = None) -> None:
        self._root = Path(root)
        self._logger = logger or logging.getLogger('PlanQueueStore')

    @staticmethod
    def is_valid_plan_id(plan_id: str) -> bool:
        return bool(_PLAN_ID.match(str(plan_id or '')))

    def _path(self, plan_id: str, suffix: str = _SUFFIX) -> Path:
        if not self.is_valid_plan_id(plan_id):
            raise ValueError(f'not a usable plan id: {plan_id!r}')
        return self._root / f'{plan_id}{suffix}'

    def save(self, plan: Plan) -> bool:
        return atomic_write_json(
            self._path(plan.plan_id), plan.to_dict(), logger=self._logger, label='plan',
        )

    def load_all(self) -> list[Plan]:
        if not self._root.is_dir():
            return []
        plans = []
        for path in sorted(self._root.glob(f'*{_SUFFIX}')):
            if not self.is_valid_plan_id(path.name[:-len(_SUFFIX)]):
                continue
            plan = self._read(path)
            if plan is not None:
                plans.append(plan)
        return plans

    def delete(self, plan_id: str) -> None:
        for suffix in (_SUFFIX, _REPLY_SUFFIX):
            path = self._path(plan_id, suffix)
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def save_reply(self, plan_id: str, text: str) -> None:
        """Keep the proposer's last reply, so a failed proposal can be inspected."""
        path = self._path(plan_id, _REPLY_SUFFIX)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
        except OSError:
            self._logger.warning('could not keep the proposer reply for plan %s', plan_id)

    def _read(self, path: Path) -> Plan | None:
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self._logger.warning('skipping unreadable plan file %s', path.name)
            return None
        if not isinstance(data, dict):
            self._logger.warning('skipping plan file %s: not an object', path.name)
            return None
        plan = Plan.from_dict(data)
        if plan.plan_id != path.name[:-len(_SUFFIX)]:
            self._logger.warning('skipping plan file %s: its id does not match', path.name)
            return None
        return plan

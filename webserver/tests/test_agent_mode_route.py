"""``/api/sessions/<task_id>/agent-mode`` — the composer's Modes picker.

The stored value is the literal ``--permission-mode`` the next spawn uses, so
an unvalidated value would not fail here — it would fail at spawn, minutes
later, looking like "kato stopped responding" rather than "that mode is not a
thing". Hence the allow-list at the route.

The mode is persisted for the same reason plan mode always was: it is a
SAFETY posture. A task left on Manual must come back on Manual after a
restart, not silently drop to the permissive default.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from kato_webserver.app import create_app


class _Manager:
    def list_records(self):
        return []

    def get_record(self, task_id):  # noqa: ARG002
        return None

    def get_session(self, task_id):  # noqa: ARG002
        return None


class AgentModeRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Path(self._tmp.name) / 'plan_mode.json'
        patcher = patch.dict(os.environ, {'KATO_PLAN_MODE_PATH': str(self.store)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = create_app(session_manager=_Manager())
        self.client = self.app.test_client()

    def _set(self, mode):
        return self.client.post(
            '/api/sessions/T1/agent-mode', json={'mode': mode},
        )

    def test_defaults_to_the_configured_default(self) -> None:
        body = self.client.get('/api/sessions/T1/agent-mode').get_json()
        self.assertEqual(body['mode'], '')

    def test_round_trips_every_supported_mode(self) -> None:
        for mode in ('default', 'plan', 'bypassPermissions', 'acceptEdits', ''):
            with self.subTest(mode=mode):
                self.assertEqual(self._set(mode).status_code, 200)
                self.assertEqual(
                    self.client.get('/api/sessions/T1/agent-mode').get_json()['mode'],
                    mode,
                )

    def test_unknown_mode_is_refused_at_the_route(self) -> None:
        response = self._set('yolo')
        self.assertEqual(response.status_code, 400)
        self.assertIn('allowed', response.get_json())
        # …and nothing was stored, so the next spawn is unaffected.
        self.assertEqual(
            self.client.get('/api/sessions/T1/agent-mode').get_json()['mode'], '',
        )

    def test_the_mode_is_persisted_for_the_next_boot(self) -> None:
        self._set('plan')
        self.assertEqual(json.loads(self.store.read_text()), {'T1': 'plan'})

    def test_a_persisted_mode_is_reloaded_at_boot(self) -> None:
        self.store.write_text(json.dumps({'T1': 'default', 'T2': 'plan'}))
        app = create_app(session_manager=_Manager())
        client = app.test_client()
        self.assertEqual(
            client.get('/api/sessions/T1/agent-mode').get_json()['mode'], 'default',
        )
        self.assertEqual(
            client.get('/api/sessions/T2/agent-mode').get_json()['mode'], 'plan',
        )

    def test_a_legacy_plan_lock_survives_the_upgrade(self) -> None:
        # The pre-modes file was a list of plan-locked task ids. Reading it as
        # "no modes set" would quietly release a safety lock on upgrade.
        self.store.write_text(json.dumps(['T9']))
        app = create_app(session_manager=_Manager())
        client = app.test_client()
        self.assertEqual(
            client.get('/api/sessions/T9/agent-mode').get_json()['mode'], 'plan',
        )

    def test_plan_is_just_one_of_the_modes(self) -> None:
        # There is no second surface to disagree with any more: the boolean
        # ``/plan-mode`` pair that used to describe this same override is gone.
        # "Is this task plan-locked" is ``mode == 'plan'``.
        self._set('plan')
        self.assertEqual(
            self.client.get('/api/sessions/T1/agent-mode').get_json()['mode'], 'plan',
        )
        self._set('')
        self.assertEqual(
            self.client.get('/api/sessions/T1/agent-mode').get_json()['mode'], '',
        )

    def test_the_plan_mode_route_pair_is_gone(self) -> None:
        # It was unreachable from any UI, and its POST wrote '' over whatever
        # mode was stored — including a bypassPermissions lock — then stopped
        # the live session. Deleted, not deprecated.
        self.assertEqual(
            self.client.get('/api/sessions/T1/plan-mode').status_code, 404,
        )
        self.assertEqual(
            self.client.post(
                '/api/sessions/T1/plan-mode', json={'plan_mode': False},
            ).status_code, 404,
        )

    def test_forget_clears_the_persisted_plan_lock(self) -> None:
        """Deleting a task must not leave it plan-locked for its next life.

        This guard went out with the /plan-mode route tests. The production
        code was untouched, but nothing asserted it any more — and the failure
        is silent: the autonomous spawn re-reads plan_mode.json per spawn, so a
        resurrected lock applies on the next 180s scan tick with no restart and
        nothing on screen to explain why the agent will not edit.
        """
        self._set('plan')
        self.assertEqual(
            self.client.get('/api/sessions/T1/agent-mode').get_json()['mode'], 'plan',
        )

        # The forget route needs a workspace manager wired, or it 503s before
        # reaching the clear.
        workspace = SimpleNamespace(
            get=lambda task_id: None,
            delete=lambda task_id: None,
            workspace_path=lambda task_id: Path('/missing'),
        )
        app = create_app(session_manager=_Manager(), workspace_manager=workspace)
        client = app.test_client()
        client.post('/api/sessions/T1/agent-mode', json={'mode': 'plan'})
        self.assertEqual(
            client.get('/api/sessions/T1/agent-mode').get_json()['mode'], 'plan',
        )

        response = client.delete('/api/sessions/T1/workspace')
        self.assertNotEqual(response.status_code, 503)

        self.assertEqual(
            client.get('/api/sessions/T1/agent-mode').get_json()['mode'], '',
        )
        # And it stays cleared across a restart — the lock is read off disk.
        reborn = create_app(session_manager=_Manager()).test_client()
        self.assertEqual(
            reborn.get('/api/sessions/T1/agent-mode').get_json()['mode'], '',
        )

    def test_set_returns_503_when_the_override_store_is_unwired(self) -> None:
        # Also lost with the deleted suite. A host with no override store must
        # say so rather than accept a mode it will silently drop.
        app = create_app(session_manager=_Manager())
        app.config['TASK_PLAN_MODE_OVERRIDES'] = None
        response = app.test_client().post(
            '/api/sessions/T1/agent-mode', json={'mode': 'plan'},
        )
        self.assertEqual(response.status_code, 503)


UNKNOWN_USAGE = {
    # ``baseline_tokens`` (the cost indicator's floor) is part of the one shape
    # every path returns — zeros here, same as the rest.
    'used_tokens': 0, 'limit_tokens': 0, 'model': '', 'baseline_tokens': 0,
}


class _Recorded(_Manager):
    def get_record(self, task_id):  # noqa: ARG002
        return {'task_id': 'T1'}


class SessionContextUsageTests(unittest.TestCase):
    """``/api/sessions/<id>/context-usage`` — the composer meter's reading.

    It has its OWN route because it used to be read off ``/api/sessions/<id>``
    with the rest of the payload discarded — and that payload carries
    ``recent_events``, the whole session transcript rather than a bounded tail.
    The meter refreshes at every turn boundary, so four numbers were costing
    the entire conversation, at its longest, exactly when the operator was
    waiting on the next turn.
    """

    def setUp(self) -> None:
        self.client = create_app(session_manager=_Recorded()).test_client()

    def test_reports_unknown_not_zero_percent_with_no_session(self) -> None:
        # Zeros render as "unknown". Reporting 0% used would read as
        # "plenty of room" — the opposite of the truth for a full window.
        body = self.client.get('/api/sessions/T1/context-usage').get_json()
        self.assertEqual(body, UNKNOWN_USAGE)

    def test_the_payload_carries_no_transcript(self) -> None:
        # THE POINT of the route. ``recent_events`` is unbounded, and every
        # event's ``raw`` is a full CLI stream-json object — tool inputs and
        # outputs included.
        body = self.client.get('/api/sessions/T1/context-usage').get_json()
        self.assertNotIn('recent_events', body)
        self.assertEqual(set(body), set(UNKNOWN_USAGE))

    def test_an_unknown_task_is_a_404(self) -> None:
        app = create_app(session_manager=_Manager())
        self.assertEqual(
            app.test_client().get('/api/sessions/nope/context-usage').status_code,
            404,
        )

    def test_the_fat_route_still_carries_it_for_its_own_consumers(self) -> None:
        # The field was not moved OFF /api/sessions/<id> — other readers of
        # that record still get it. Only the meter stopped paying for it.
        body = self.client.get('/api/sessions/T1').get_json()
        self.assertEqual(body['context_usage'], UNKNOWN_USAGE)


if __name__ == '__main__':
    unittest.main()

"""The model picker shows the model a spawn will actually run.

``GET /api/sessions/<task>/model`` answers the operator's own pick when there is
one, else the model the ticket's ``kato:model:`` tag selects — never a blank
that the picker would paper over with some other model.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from flask import Flask

from kato_webserver.app import _build_fallback_manager, _tagged_model, create_app
from tests.chaos_lib import build_real_agent_service, materialize_workspace


class TaggedModelHelperTests(unittest.TestCase):
    @staticmethod
    def _app(runner) -> Flask:
        app = Flask(__name__)
        app.config['PLANNING_SESSION_RUNNER'] = runner
        return app

    def test_asks_the_runner_for_the_tasks_model(self) -> None:
        runner = SimpleNamespace(tagged_model=lambda task_id: 'claude-sonnet-5-5')
        self.assertEqual(_tagged_model(self._app(runner), 'T1'), 'claude-sonnet-5-5')

    def test_no_runner_or_a_runner_without_the_question_is_no_model(self) -> None:
        self.assertEqual(_tagged_model(self._app(None), 'T1'), '')
        self.assertEqual(_tagged_model(self._app(SimpleNamespace()), 'T1'), '')

    def test_a_runner_that_raises_is_logged_and_is_no_model(self) -> None:
        runner = MagicMock()
        runner.tagged_model.side_effect = RuntimeError('boom')
        app = self._app(runner)
        app.logger = MagicMock()
        self.assertEqual(_tagged_model(app, 'T1'), '')
        app.logger.exception.assert_called_once()


class SessionModelRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-model-tag-')
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        agent_service, workspaces = build_real_agent_service(root)
        materialize_workspace(workspaces, 'T1', repository_ids=['alpha'])
        self.app = create_app(
            session_manager=_build_fallback_manager(str(root / 'sessions')),
            workspace_manager=workspaces,
            agent_service=agent_service,
            fallback_state_dir=str(root / 'sessions'),
        )
        self.app.config['PLANNING_SESSION_RUNNER'] = SimpleNamespace(
            tagged_model=lambda task_id: 'claude-sonnet-5-5' if task_id == 'T1' else '',
        )
        self.client = self.app.test_client()

    def test_the_picker_shows_the_tagged_model_when_the_operator_has_not_picked(self) -> None:
        self.assertEqual(
            self.client.get('/api/sessions/T1/model').get_json(), {'model': 'claude-sonnet-5-5'},
        )

    def test_the_operators_own_pick_outranks_the_tag(self) -> None:
        self.client.post('/api/sessions/T1/model', json={'model': 'haiku'})
        self.assertEqual(self.client.get('/api/sessions/T1/model').get_json(), {'model': 'haiku'})

    def test_an_untagged_task_still_answers_blank_so_the_default_shows(self) -> None:
        self.assertEqual(self.client.get('/api/sessions/T2/model').get_json(), {'model': ''})


if __name__ == '__main__':
    unittest.main()

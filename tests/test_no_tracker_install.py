"""An install with no issue tracker: every task is created in kato itself.

Kato can take tasks from its own "New task" tab, so a tracker is optional —
but first-run setup demanded one: an unset ``KATO_ISSUE_PLATFORM`` meant
YouTrack, and YouTrack meant its URL, token, project and assignee before kato
would leave setup mode. ``KATO_ISSUE_PLATFORM=local`` is the install that has
none: no tracker keys are required, and kato runs on an empty tracker under
the same local-task routing a tracker install uses.

Real composed config, real routing services, a real store on disk.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hydra import compose, initialize_config_dir
from omegaconf import OmegaConf

from kato_core_lib.data_layers.data_access.task_data_access import TaskDataAccess
from kato_core_lib.data_layers.service.local_task_routing import (
    LocalAwareTaskService,
    LocalAwareTaskStateService,
    NoTrackerClient,
)
from kato_core_lib.helpers.issue_platform_utils import NO_TRACKER_ISSUE_PLATFORM, is_no_tracker
from kato_core_lib.kato_core_lib import KatoCoreLib
from kato_core_lib.validate_env import validate_agent_env
from local_task_core_lib.local_task_core_lib.data.local_task import LocalTaskState
from local_task_core_lib.local_task_core_lib.store import LocalTaskStore
from vcs_provider_contracts.vcs_provider_contracts.issue_provider import IssueProvider

_CONFIG_DIR = str(Path(__file__).resolve().parents[1] / 'kato_core_lib' / 'config')


def _kato_config(**environment):
    """The blocks of kato's real config these tests read, resolved with
    ``environment`` set.

    Resolved inside the patch: OmegaConf reads ``${oc.env:…}`` lazily, so a
    config read after it would see the real environment instead.
    """
    with patch.dict(os.environ, environment), initialize_config_dir(
        config_dir=_CONFIG_DIR, version_base=None,
    ):
        kato = compose(config_name='kato_core_lib').kato
        return OmegaConf.create({
            'issue_platform': kato.issue_platform,
            **{
                block: OmegaConf.to_container(kato[block], resolve=True)
                for block in ('local_tasks', 'youtrack')
            },
        })


class SetupNeedsNoTrackerKeysTests(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repositories = tmp.name

    def test_no_tracker_is_a_complete_configuration(self) -> None:
        self.assertEqual(validate_agent_env({
            'KATO_ISSUE_PLATFORM': 'local',
            'KATO_AGENT_BACKEND': 'claude',
            'REPOSITORY_ROOT_PATH': self.repositories,
        }), [])

    def test_only_what_is_not_about_a_tracker_is_still_asked(self) -> None:
        self.assertEqual(
            validate_agent_env({'KATO_ISSUE_PLATFORM': 'Local', 'KATO_AGENT_BACKEND': 'claude'}),
            ['missing required agent env var: REPOSITORY_ROOT_PATH'],
        )

    def test_an_unset_platform_still_means_youtrack(self) -> None:
        # Existing installs that never set it keep polling their tracker.
        errors = validate_agent_env({
            'KATO_AGENT_BACKEND': 'claude', 'REPOSITORY_ROOT_PATH': self.repositories,
        })
        self.assertIn('missing required agent env var: YOUTRACK_API_TOKEN', errors)


class TheNoTrackerInstallRunsTests(unittest.TestCase):
    """kato's own wiring, from the composed config down to the store."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = LocalTaskStore(Path(tmp.name) / 'local_tasks.json')
        kato = _kato_config(KATO_ISSUE_PLATFORM='local')
        self.client, ticket_cfg = KatoCoreLib._build_ticket_client(kato, 3)
        data_access = TaskDataAccess(ticket_cfg, self.client)
        self.tasks = LocalAwareTaskService(ticket_cfg, data_access, self.store)
        self.states = LocalAwareTaskStateService(ticket_cfg, data_access, self.store)

    def test_it_runs_on_the_empty_tracker(self) -> None:
        self.assertIsInstance(self.client, NoTrackerClient)
        self.assertIsInstance(self.client, IssueProvider)
        self.assertEqual(self.tasks.provider_name, 'local')
        self.tasks.validate_connection()  # nothing to connect to: never fails

    def test_the_tasks_are_the_local_ones_through_their_whole_life(self) -> None:
        queued = self.store.create('Fix the login page')
        started = self.store.create('Audit log', state=LocalTaskState.IN_PROGRESS)

        # The autonomous scan's queue, and the "+ Add task" backlog.
        self.assertEqual([t.id for t in self.tasks.get_assigned_tasks()], [queued.id])
        self.assertEqual(
            sorted(t.id for t in self.tasks.list_all_assigned_tasks()),
            sorted([queued.id, started.id]),
        )
        self.states.move_task_to_review(started.id)
        self.assertEqual([t.id for t in self.tasks.get_review_tasks()], [started.id])
        self.tasks.add_comment(started.id, 'Pull request created')
        self.assertEqual(self.store.get(started.id).comments[-1].body, 'Pull request created')

    def test_a_ticket_from_a_tracker_it_no_longer_has_is_refused_plainly(self) -> None:
        # A workspace left from before the switch: say so, never pretend.
        for write in (
            lambda: self.tasks.add_comment('UNA-3242', 'done'),
            lambda: self.tasks.add_tag('UNA-3242', 'x'),
            lambda: self.tasks.remove_tag('UNA-3242', 'x'),
            lambda: self.states.move_task_to_review('UNA-3242'),
        ):
            with self.assertRaisesRegex(ValueError, 'UNA-3242 is not a task kato has'):
                write()
        self.assertEqual(self.tasks.download_image_attachments('UNA-3242', '/tmp/x'), [])

    def test_a_tracker_install_still_gets_its_tracker(self) -> None:
        kato = _kato_config(KATO_ISSUE_PLATFORM='youtrack')
        client, ticket_cfg = KatoCoreLib._build_ticket_client(kato, 3)
        self.assertNotIsInstance(client, NoTrackerClient)
        self.assertEqual(ticket_cfg, kato.youtrack)


class NoTrackerNameTests(unittest.TestCase):

    def test_the_name_in_any_case(self) -> None:
        self.assertEqual(NO_TRACKER_ISSUE_PLATFORM, 'local')
        for value, expected in (('local', True), (' LOCAL ', True), ('youtrack', False),
                                ('', False), (None, False)):
            with self.subTest(value=value):
                self.assertEqual(is_no_tracker(value), expected)


if __name__ == '__main__':
    unittest.main()

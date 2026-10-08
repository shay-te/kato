"""A local task from creation to done, the way a host drives it.

Real store on a real file; several store instances and threads at once, as a
web server and a background scan share one file.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from local_task_core_lib.local_task_core_lib.data.local_task import LocalTaskState
from local_task_core_lib.local_task_core_lib.store import LocalTaskStore


class LifecycleFlowTests(unittest.TestCase):

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'tasks.json'

    def test_create_work_review_done(self) -> None:
        ui = LocalTaskStore(self.path)       # where the operator creates it
        scan = LocalTaskStore(self.path)     # what the pipeline reads

        task = ui.create(
            'Add retry to the export job',
            'The export job fails on timeouts.',
            tags=['repo:core-lib', 'repo:export-service'],
            state=LocalTaskState.IN_PROGRESS,
        )
        self.assertEqual([t.id for t in scan.list([LocalTaskState.IN_PROGRESS])], [task.id])
        self.assertEqual(scan.list([LocalTaskState.OPEN]), [])

        scan.add_tag(task.id, 'repo:web-client')           # a repo added mid-task
        scan.add_comment(task.id, 'Work started.', author='bot', author_id='bot')
        scan.set_state(task.id, LocalTaskState.REVIEW)      # the PR is open
        in_review = ui.list([LocalTaskState.REVIEW])
        self.assertEqual([t.id for t in in_review], [task.id])
        self.assertEqual(
            in_review[0].tags, ['repo:core-lib', 'repo:export-service', 'repo:web-client'],
        )
        self.assertEqual([c.body for c in in_review[0].comments], ['Work started.'])

        ui.set_state(task.id, LocalTaskState.DONE)
        self.assertEqual(scan.list([LocalTaskState.REVIEW]), [])
        self.assertEqual(scan.get(task.id).state, LocalTaskState.DONE)

        self.assertTrue(ui.delete(task.id))
        self.assertIsNone(scan.get(task.id))
        self.assertEqual(ui.create('next').id, 'LOCAL-2')

    def test_concurrent_creates_never_share_an_id_or_lose_a_task(self) -> None:
        stores = [LocalTaskStore(self.path) for _ in range(4)]
        made: list[str] = []
        guard = threading.Lock()

        def create_many(store: LocalTaskStore, worker: int) -> None:
            for index in range(10):
                task = store.create(f'task {worker}-{index}')
                with guard:
                    made.append(task.id)

        threads = [
            threading.Thread(target=create_many, args=(store, worker))
            for worker, store in enumerate(stores)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(made), 40)
        self.assertEqual(len(set(made)), 40)
        stored = LocalTaskStore(self.path).list()
        self.assertEqual(sorted(t.id for t in stored), sorted(made))

    def test_concurrent_changes_to_one_task_all_land(self) -> None:
        task = LocalTaskStore(self.path).create('shared')
        stores = [LocalTaskStore(self.path) for _ in range(4)]

        def comment(store: LocalTaskStore, worker: int) -> None:
            for index in range(10):
                store.add_comment(task.id, f'{worker}-{index}')

        threads = [threading.Thread(target=comment, args=(s, w)) for w, s in enumerate(stores)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(LocalTaskStore(self.path).get(task.id).comments), 40)


if __name__ == '__main__':
    unittest.main()

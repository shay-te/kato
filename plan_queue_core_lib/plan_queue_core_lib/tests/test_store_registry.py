"""Plans on disk and in memory: saved only when changed, never written blind."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from plan_queue_core_lib.plan_queue_core_lib.data.plan import Plan, PlanStatus
from plan_queue_core_lib.plan_queue_core_lib.errors import ErrorKind, PlanQueueError
from plan_queue_core_lib.plan_queue_core_lib.registry import PlanRegistry
from plan_queue_core_lib.plan_queue_core_lib.store import PlanQueueStore
from plan_queue_core_lib.plan_queue_core_lib.tests.fakes import ManualClock


def _plan(now: float = 1.0, source: str = 'S-1') -> Plan:
    return Plan.new(source, plan_markdown='x', repository_ids=['r'], now=now)


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix='plan-store-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'nested' / 'plans'
        self.store = PlanQueueStore(self.root)

    def test_round_trip_and_delete(self) -> None:
        self.assertEqual(self.store.load_all(), [])  # no folder yet
        plan = _plan()
        self.assertTrue(self.store.save(plan))
        self.store.save_reply(plan.plan_id, 'raw reply')
        [loaded] = self.store.load_all()
        self.assertEqual(loaded.to_dict(), plan.to_dict())
        self.assertTrue((self.root / f'{plan.plan_id}.reply.txt').is_file())
        self.store.delete(plan.plan_id)
        self.store.delete(plan.plan_id)  # deleting twice is fine
        self.assertEqual(list(self.root.iterdir()), [])

    def test_ids_are_validated(self) -> None:
        self.assertFalse(PlanQueueStore.is_valid_plan_id('../etc'))
        self.assertFalse(PlanQueueStore.is_valid_plan_id(None))
        with self.assertRaises(ValueError):
            self.store.delete('../../evil')
        bad = _plan()
        bad.plan_id = 'not-hex'
        with self.assertRaises(ValueError):
            self.store.save(bad)

    def test_unusable_files_are_skipped_and_left_alone(self) -> None:
        good = _plan()
        self.store.save(good)
        torn = self.root / ('a' * 32 + '.json')
        torn.write_text('{"plan_id": ', encoding='utf-8')
        listy = self.root / ('b' * 32 + '.json')
        listy.write_text('[1]', encoding='utf-8')
        mismatched = self.root / ('c' * 32 + '.json')
        mismatched.write_text(json.dumps({'plan_id': 'd' * 32}), encoding='utf-8')
        (self.root / 'notes.json').write_text('{}', encoding='utf-8')
        with self.assertLogs('PlanQueueStore', level='WARNING'):
            plans = self.store.load_all()
        self.assertEqual([plan.plan_id for plan in plans], [good.plan_id])
        self.assertEqual(torn.read_text(encoding='utf-8'), '{"plan_id": ')

    def test_a_reply_that_cannot_be_written_is_only_logged(self) -> None:
        self.root.parent.mkdir(parents=True)
        self.root.write_text('a file where the folder should be', encoding='utf-8')
        with self.assertLogs('PlanQueueStore', level='WARNING'):
            self.store.save_reply('e' * 32, 'reply')


class RegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix='plan-registry-')
        self.addCleanup(tmp.cleanup)
        self.store = PlanQueueStore(tmp.name)
        self.clock = ManualClock(100.0)
        self.registry = PlanRegistry(self.store, clock=self.clock)

    def test_add_get_find_all_remove(self) -> None:
        later = self.registry.add(_plan(now=5.0, source='S-2'))
        earlier = self.registry.add(_plan(now=1.0, source='S-1'))
        self.assertEqual([plan.source_task_id for plan in self.registry.all()], ['S-1', 'S-2'])
        copy = self.registry.get(later.plan_id)
        copy.title = 'changed on the copy'
        self.assertNotEqual(self.registry.get(later.plan_id).title, 'changed on the copy')
        self.assertIsNone(self.registry.find('f' * 32))
        self.registry.remove(earlier.plan_id)
        with self.assertRaises(PlanQueueError) as caught:
            self.registry.get(earlier.plan_id)
        self.assertEqual(caught.exception.kind, ErrorKind.NOT_FOUND)
        self.assertIs(self.registry.store, self.store)

    def test_loads_what_the_store_holds(self) -> None:
        plan = self.registry.add(_plan())
        again = PlanRegistry(self.store, clock=self.clock)
        self.assertEqual(again.get(plan.plan_id).plan_id, plan.plan_id)

    def test_a_change_is_saved_and_stamped(self) -> None:
        plan = self.registry.add(_plan())

        def change(live):
            live.title = 'new'
            return 'result'

        self.clock.advance(5)
        self.assertEqual(self.registry.mutate(plan.plan_id, change), 'result')
        [stored] = self.store.load_all()
        self.assertEqual((stored.title, stored.updated_at), ('new', 105.0))

    def test_no_change_writes_nothing(self) -> None:
        plan = self.registry.add(_plan())
        path = Path(self.store._path(plan.plan_id))
        path.unlink()
        self.registry.mutate(plan.plan_id, lambda live: None)
        self.assertFalse(path.exists())

    def test_a_refused_change_is_undone(self) -> None:
        plan = self.registry.add(_plan())

        def refuse(live):
            live.status = PlanStatus.CANCELLED
            raise PlanQueueError('no')

        with self.assertRaises(PlanQueueError):
            self.registry.mutate(plan.plan_id, refuse)
        self.assertEqual(self.registry.get(plan.plan_id).status, PlanStatus.PROPOSING)


class ErrorTests(unittest.TestCase):
    def test_default_kind_is_conflict(self) -> None:
        self.assertEqual(PlanQueueError('x').kind, ErrorKind.CONFLICT)


if __name__ == '__main__':
    unittest.main()

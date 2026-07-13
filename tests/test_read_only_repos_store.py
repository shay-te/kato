"""Tests for the persistent per-task read-only-repos store.

This is what lets one un-pushable reference repo NOT reject the task: the
preflight records it here, the publish step skips it, and the planning UI's
file tree badges it. Re-check clears a repo once push access is granted.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_core_lib.helpers import read_only_repos_store as store


class ReadOnlyReposStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = str(Path(self._tmp.name) / 'sub' / 'read_only_repos.json')
        ctx = patch.dict(os.environ, {'KATO_READ_ONLY_REPOS_PATH': self.path})
        ctx.start()
        self.addCleanup(ctx.stop)

    def test_empty_when_no_file(self) -> None:
        self.assertEqual(store.read_only_repos('UNA-1'), set())
        self.assertFalse(store.is_read_only('UNA-1', 'repo'))

    def test_set_and_read_persists(self) -> None:
        store.set_read_only_repos('UNA-2742', ['kafka-connect-elasticsearch', 'kafka-connect-solr'])
        self.assertEqual(
            store.read_only_repos('UNA-2742'),
            {'kafka-connect-elasticsearch', 'kafka-connect-solr'},
        )
        self.assertTrue(store.is_read_only('UNA-2742', 'kafka-connect-elasticsearch'))
        self.assertTrue(Path(self.path).is_file())

    def test_task_id_match_is_case_insensitive(self) -> None:
        store.set_read_only_repos('UNA-2742', ['ext-lib'])
        # records are lowercased on disk; the platform yields uppercase ids
        self.assertTrue(store.is_read_only('una-2742', 'ext-lib'))
        self.assertEqual(store.read_only_repos(' UNA-2742 '), {'ext-lib'})

    def test_clear_single_repo(self) -> None:
        store.set_read_only_repos('UNA-1', ['a', 'b'])
        store.clear_read_only_repo('UNA-1', 'a')
        self.assertEqual(store.read_only_repos('UNA-1'), {'b'})

    def test_set_empty_clears_the_task(self) -> None:
        store.set_read_only_repos('UNA-1', ['a'])
        store.set_read_only_repos('UNA-1', [])
        self.assertEqual(store.read_only_repos('UNA-1'), set())

    def test_forget_task_drops_all(self) -> None:
        store.set_read_only_repos('UNA-1', ['a', 'b'])
        store.forget_task('UNA-1')
        self.assertEqual(store.read_only_repos('UNA-1'), set())

    def test_corrupt_file_reads_as_empty(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.path).write_text('not json{', encoding='utf-8')
        self.assertEqual(store.read_only_repos('UNA-1'), set())
        store.set_read_only_repos('UNA-1', ['a'])  # recovers cleanly
        self.assertEqual(store.read_only_repos('UNA-1'), {'a'})

    def test_concurrent_tasks_never_lose_each_others_entries(self) -> None:
        # Regression: set_read_only_repos() used to have no lock around its
        # read-modify-write cycle — two tasks finishing preflight around
        # the same moment (the default KATO_MAX_PARALLEL_TASKS=2 makes
        # this a routine scan-cycle occurrence, not a rare race) could
        # both read the old file before either wrote, so the second
        # writer's save silently reverted the first task's entry. Now
        # under a lock (mirrors tool_decision_store.py's pattern): every
        # concurrent task's entry must survive.
        import threading

        def writer(i: int) -> None:
            store.set_read_only_repos(f'task-{i}', [f'repo-{i}'])

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for i in range(12):
            self.assertEqual(
                store.read_only_repos(f'task-{i}'), {f'repo-{i}'},
                f'entry for task-{i} was lost',
            )


if __name__ == '__main__':
    unittest.main()

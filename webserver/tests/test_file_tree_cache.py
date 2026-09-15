"""FileTreeCache — the server's copy of each task's Files tree.

Real temp directories and real files throughout; the only patch is a pass-through
counter on the disk write, to prove an unchanged tree is not rewritten.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_webserver import file_tree_cache
from kato_webserver.file_tree_cache import CACHE_HIT_KEY, FileTreeCache

PAYLOAD = {
    'repository_ids': ['client'],
    'trees': [{'repo_id': 'client', 'cwd': '/w/client', 'tree': [{'name': 'a.py'}]}],
}


class FileTreeCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = Path(self._tmp.name) / 'cache'

    def _served(self, cache: FileTreeCache, task_id: str):
        body = cache.cached_body(task_id)
        return None if body is None else json.loads(body)

    def test_nothing_stored_means_nothing_served(self) -> None:
        self.assertIsNone(FileTreeCache(self.directory).cached_body('PROJ-1'))

    def test_a_stored_tree_is_served_marked_as_a_cache_hit(self) -> None:
        cache = FileTreeCache(self.directory)
        cache.store('PROJ-1', PAYLOAD)
        self.assertEqual(self._served(cache, 'PROJ-1'), {**PAYLOAD, CACHE_HIT_KEY: True})

    def test_the_fresh_body_carries_no_cache_flag(self) -> None:
        body = FileTreeCache(self.directory).store('PROJ-1', PAYLOAD)
        self.assertEqual(json.loads(body), PAYLOAD)

    def test_the_copy_survives_a_restart(self) -> None:
        FileTreeCache(self.directory).store('PROJ-1', PAYLOAD)
        restarted = FileTreeCache(self.directory)
        self.assertEqual(self._served(restarted, 'PROJ-1'), {**PAYLOAD, CACHE_HIT_KEY: True})

    def test_a_restored_copy_is_byte_identical_to_the_fresh_build(self) -> None:
        # The client compares bytes to decide whether to re-render; a key-order
        # difference after a restart would repaint an unchanged tree.
        fresh = FileTreeCache(self.directory).store('PROJ-1', {'b': 1, 'a': {'d': 2, 'c': 3}})
        restored = FileTreeCache(self.directory).cached_body('PROJ-1')
        self.assertEqual(restored, b'{"cache_hit":true,' + fresh[1:])

    def test_without_a_directory_the_copy_lives_only_in_that_instance(self) -> None:
        FileTreeCache().store('PROJ-1', PAYLOAD)
        self.assertIsNone(FileTreeCache().cached_body('PROJ-1'))
        self.assertFalse(self.directory.exists())

    def test_forget_drops_the_copy_from_memory_and_disk(self) -> None:
        cache = FileTreeCache(self.directory)
        cache.store('PROJ-1', PAYLOAD)
        cache.forget('PROJ-1')
        self.assertIsNone(cache.cached_body('PROJ-1'))
        self.assertIsNone(FileTreeCache(self.directory).cached_body('PROJ-1'))

    def test_forgetting_an_unknown_task_is_harmless(self) -> None:
        FileTreeCache(self.directory).forget('NEVER-SEEN')
        FileTreeCache().forget('NEVER-SEEN')

    def test_an_unreadable_copy_is_ignored(self) -> None:
        self.directory.mkdir(parents=True)
        (self.directory / 'PROJ-1.json').write_text('not json{', encoding='utf-8')
        self.assertIsNone(FileTreeCache(self.directory).cached_body('PROJ-1'))

    def test_a_copy_that_is_not_an_object_is_ignored(self) -> None:
        self.directory.mkdir(parents=True)
        (self.directory / 'PROJ-1.json').write_text('[1, 2]', encoding='utf-8')
        self.assertIsNone(FileTreeCache(self.directory).cached_body('PROJ-1'))

    def test_an_unchanged_tree_is_not_rewritten(self) -> None:
        cache = FileTreeCache(self.directory)
        with patch.object(
            file_tree_cache, 'atomic_write_json', wraps=file_tree_cache.atomic_write_json,
        ) as write:
            cache.store('PROJ-1', PAYLOAD)
            cache.store('PROJ-1', PAYLOAD)
            cache.store('PROJ-1', {**PAYLOAD, 'repository_ids': ['client', 'backend']})
        self.assertEqual(write.call_count, 2)

    def test_memory_is_bounded(self) -> None:
        cache = FileTreeCache(max_in_memory=1)
        cache.store('PROJ-1', PAYLOAD)
        cache.store('PROJ-2', PAYLOAD)
        self.assertIsNone(cache.cached_body('PROJ-1'))
        self.assertIsNotNone(cache.cached_body('PROJ-2'))

    def test_a_tree_dropped_from_memory_is_still_served_from_disk(self) -> None:
        cache = FileTreeCache(self.directory, max_in_memory=1)
        cache.store('PROJ-1', PAYLOAD)
        cache.store('PROJ-2', PAYLOAD)
        self.assertEqual(self._served(cache, 'PROJ-1'), {**PAYLOAD, CACHE_HIT_KEY: True})

    def test_a_task_id_cannot_write_outside_the_directory(self) -> None:
        FileTreeCache(self.directory).store('../../escape', PAYLOAD)
        written = list(Path(self._tmp.name).rglob('*.json'))
        self.assertEqual([path.parent for path in written], [self.directory])
        self.assertIsNotNone(FileTreeCache(self.directory).cached_body('../../escape'))

    def test_an_id_with_no_usable_name_gets_no_disk_copy(self) -> None:
        cache = FileTreeCache(self.directory)
        cache.store('..', PAYLOAD)
        self.assertFalse(self.directory.exists())
        self.assertIsNotNone(cache.cached_body('..'))

    def test_an_empty_payload_is_still_valid_json_when_marked(self) -> None:
        cache = FileTreeCache()
        cache.store('PROJ-1', {})
        self.assertEqual(self._served(cache, 'PROJ-1'), {CACHE_HIT_KEY: True})


if __name__ == '__main__':
    unittest.main()

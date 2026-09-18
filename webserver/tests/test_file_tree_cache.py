"""FileTreeCache — the server's copy of each task's Files tree.

Real temp directories and real files throughout; the only patch is a pass-through
counter on the disk write, to prove an unchanged tree is not rewritten.
"""
from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_webserver import file_tree_cache
from kato_webserver.file_tree_cache import FileTreeCache

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
        entry = cache.cached(task_id)
        return None if entry is None else json.loads(entry.body)

    def test_nothing_stored_means_nothing_served(self) -> None:
        self.assertIsNone(FileTreeCache(self.directory).cached('PROJ-1'))

    def test_a_stored_tree_is_served_back(self) -> None:
        cache = FileTreeCache(self.directory)
        cache.store('PROJ-1', PAYLOAD)
        self.assertEqual(self._served(cache, 'PROJ-1'), PAYLOAD)

    def test_the_served_body_is_identical_to_the_fresh_one(self) -> None:
        # The cached answer and a fresh build of the same tree must be the same
        # bytes with the same tag: that is what lets the client's follow-up
        # request come back as an empty 304 instead of the whole tree again.
        cache = FileTreeCache(self.directory)
        fresh = cache.store('PROJ-1', PAYLOAD)
        served = cache.cached('PROJ-1')
        self.assertEqual(served.body, fresh.body)
        self.assertEqual(served.etag, fresh.etag)

    def test_the_copy_survives_a_restart(self) -> None:
        FileTreeCache(self.directory).store('PROJ-1', PAYLOAD)
        restarted = FileTreeCache(self.directory)
        self.assertEqual(self._served(restarted, 'PROJ-1'), PAYLOAD)

    def test_a_restored_copy_is_byte_identical_to_the_fresh_build(self) -> None:
        # The ETag is a hash of these bytes; a key-order difference after a
        # restart would make an unchanged tree look new to every client.
        fresh = FileTreeCache(self.directory).store('PROJ-1', {'b': 1, 'a': {'d': 2, 'c': 3}})
        restored = FileTreeCache(self.directory).cached('PROJ-1')
        self.assertEqual(restored.body, fresh.body)
        self.assertEqual(restored.etag, fresh.etag)

    def test_a_different_tree_gets_a_different_tag(self) -> None:
        cache = FileTreeCache(self.directory)
        first = cache.store('PROJ-1', PAYLOAD)
        second = cache.store('PROJ-1', {**PAYLOAD, 'repository_ids': ['client', 'backend']})
        self.assertNotEqual(first.etag, second.etag)

    def test_an_unchanged_tree_keeps_its_tag_and_is_not_recomputed(self) -> None:
        # The active task is rebuilt on every poll. Rehashing, recompressing
        # and rewriting identical megabytes each time would be pure churn.
        cache = FileTreeCache(self.directory)
        first = cache.store('PROJ-1', PAYLOAD)
        second = cache.store('PROJ-1', dict(PAYLOAD))
        self.assertIs(first, second)

    def test_the_gzipped_form_decompresses_to_the_body(self) -> None:
        entry = FileTreeCache(self.directory).store('PROJ-1', PAYLOAD)
        self.assertEqual(gzip.decompress(entry.gzipped), entry.body)
        self.assertEqual(json.loads(gzip.decompress(entry.gzipped)), PAYLOAD)

    def test_a_real_tree_compresses_well(self) -> None:
        # A tree is mostly repeated directory names, which is the shape gzip is
        # best at. Pinned so a future encoding change that destroys that (and
        # with it the point of compressing at all) fails here.
        payload = {'trees': [{
            'repo_id': 'client', 'cwd': '/w/client',
            'tree': [{
                'name': f'package-{index}',
                'children': [{'name': 'index.js'}, {'name': 'README.md'}],
            } for index in range(200)],
        }]}
        entry = FileTreeCache(self.directory).store('PROJ-1', payload)
        self.assertLess(len(entry.gzipped) * 4, len(entry.body))

    def test_without_a_directory_the_copy_lives_only_in_that_instance(self) -> None:
        FileTreeCache().store('PROJ-1', PAYLOAD)
        self.assertIsNone(FileTreeCache().cached('PROJ-1'))
        self.assertFalse(self.directory.exists())

    def test_forget_drops_the_copy_from_memory_and_disk(self) -> None:
        cache = FileTreeCache(self.directory)
        cache.store('PROJ-1', PAYLOAD)
        cache.forget('PROJ-1')
        self.assertIsNone(cache.cached('PROJ-1'))
        self.assertIsNone(FileTreeCache(self.directory).cached('PROJ-1'))

    def test_forgetting_an_unknown_task_is_harmless(self) -> None:
        FileTreeCache(self.directory).forget('NEVER-SEEN')
        FileTreeCache().forget('NEVER-SEEN')

    def test_an_unreadable_copy_is_ignored(self) -> None:
        self.directory.mkdir(parents=True)
        (self.directory / 'PROJ-1.json').write_text('not json{', encoding='utf-8')
        self.assertIsNone(FileTreeCache(self.directory).cached('PROJ-1'))

    def test_a_copy_that_is_not_an_object_is_ignored(self) -> None:
        self.directory.mkdir(parents=True)
        (self.directory / 'PROJ-1.json').write_text('[1, 2]', encoding='utf-8')
        self.assertIsNone(FileTreeCache(self.directory).cached('PROJ-1'))

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
        self.assertIsNone(cache.cached('PROJ-1'))
        self.assertIsNotNone(cache.cached('PROJ-2'))

    def test_a_tree_dropped_from_memory_is_still_served_from_disk(self) -> None:
        cache = FileTreeCache(self.directory, max_in_memory=1)
        cache.store('PROJ-1', PAYLOAD)
        cache.store('PROJ-2', PAYLOAD)
        self.assertEqual(self._served(cache, 'PROJ-1'), PAYLOAD)

    def test_a_tree_restored_from_disk_serves_every_form(self) -> None:
        FileTreeCache(self.directory).store('PROJ-1', PAYLOAD)
        entry = FileTreeCache(self.directory).cached('PROJ-1')
        self.assertTrue(entry.etag)
        self.assertEqual(gzip.decompress(entry.gzipped), entry.body)

    def test_a_task_id_cannot_write_outside_the_directory(self) -> None:
        FileTreeCache(self.directory).store('../../escape', PAYLOAD)
        written = list(Path(self._tmp.name).rglob('*.json'))
        self.assertEqual([path.parent for path in written], [self.directory])
        self.assertIsNotNone(FileTreeCache(self.directory).cached('../../escape'))

    def test_an_id_with_no_usable_name_gets_no_disk_copy(self) -> None:
        cache = FileTreeCache(self.directory)
        cache.store('..', PAYLOAD)
        self.assertFalse(self.directory.exists())
        self.assertIsNotNone(cache.cached('..'))

    def test_an_empty_payload_is_still_served(self) -> None:
        cache = FileTreeCache()
        cache.store('PROJ-1', {})
        self.assertEqual(self._served(cache, 'PROJ-1'), {})


if __name__ == '__main__':
    unittest.main()

"""Coverage for ``git_core_lib/helpers/git_clean_utils.py``."""

from __future__ import annotations

import unittest

from git_core_lib.git_core_lib.helpers.git_clean_utils import (
    generated_artifact_paths_from_status,
    status_contains_only_removable_artifacts,
    status_paths,
    validation_report_paths_from_status,
)


class StatusPathsTests(unittest.TestCase):
    def test_parses_two_letter_status_codes(self) -> None:
        out = ' M src/a.py\n?? src/b.py\nMM src/c.py\n'
        self.assertEqual(status_paths(out), ['src/a.py', 'src/b.py', 'src/c.py'])

    def test_skips_lines_shorter_than_four_chars(self) -> None:
        # Line 16: ``if len(line) < 4: continue``. Lines like '?' or 'MM '
        # (< 4 chars) get silently dropped — they can't carry a path.
        out = '?\nM\nABC\n M valid.py\n'  # only the last is parsable
        self.assertEqual(status_paths(out), ['valid.py'])

    def test_handles_rename_arrow_syntax(self) -> None:
        out = 'R  old.py -> new.py\n'
        self.assertEqual(status_paths(out), ['new.py'])

    def test_strips_trailing_slash(self) -> None:
        out = '?? src/\n'
        self.assertEqual(status_paths(out), ['src'])

    def test_skips_lines_whose_normalized_path_is_empty(self) -> None:
        # Branch 21->14: ``line[3:]`` resolves to a blank path
        # (e.g. ``'?? /'`` → ``'/'`` → ``''`` after rstrip) — the
        # entry must be dropped silently rather than appended.
        out = '?? /\n M valid.py\n'
        self.assertEqual(status_paths(out), ['valid.py'])


class ValidationReportPathsTests(unittest.TestCase):
    def test_picks_validation_report_files_only(self) -> None:
        out = ' M src/a.py\n?? .kato/validation_report.md\n'
        result = validation_report_paths_from_status(out)
        self.assertEqual(result, ['.kato/validation_report.md'])


class GeneratedArtifactPathsTests(unittest.TestCase):
    def test_picks_recognized_artifact_roots(self) -> None:
        out = '?? build/foo\n?? dist/bar\n?? src/baz.py\n'
        result = generated_artifact_paths_from_status(out)
        self.assertEqual(sorted(result), ['build', 'dist'])

    def test_excludes_validation_reports(self) -> None:
        # validation_report.md isn't treated as a generic artifact root.
        out = '?? build/foo\n?? .kato/validation_report.md\n'
        result = generated_artifact_paths_from_status(out)
        self.assertEqual(result, ['build'])

    def test_dedupes_same_root(self) -> None:
        out = '?? build/a\n?? build/b\n'
        result = generated_artifact_paths_from_status(out)
        self.assertEqual(result, ['build'])

    def test_excludes_nested_pycache_at_the_cache_dir(self) -> None:
        # The GH013 incident: committed bytecode under a NESTED __pycache__
        # (root-only matching missed it). It must collapse to the cache dir.
        out = ('?? pii_core_lib/tests/__pycache__/'
               'test_credential_patterns.cpython-311.pyc\n M src/a.py\n')
        result = generated_artifact_paths_from_status(out)
        self.assertEqual(result, ['pii_core_lib/tests/__pycache__'])

    def test_excludes_loose_pyc_and_pyo_files(self) -> None:
        out = '?? a/b/foo.pyc\n?? bar.pyo\n M keep.py\n'
        result = generated_artifact_paths_from_status(out)
        self.assertEqual(sorted(result), ['a/b/foo.pyc', 'bar.pyo'])

    def test_excludes_pytest_and_tool_caches(self) -> None:
        out = ('?? .pytest_cache/v/x\n?? pkg/.mypy_cache/y\n'
               '?? pkg/.ruff_cache/z\n')
        result = generated_artifact_paths_from_status(out)
        self.assertEqual(
            sorted(result),
            ['.pytest_cache', 'pkg/.mypy_cache', 'pkg/.ruff_cache'],
        )

    def test_real_source_py_is_not_excluded(self) -> None:
        out = ' M pii_core_lib/tests/test_credential_patterns.py\n'
        self.assertEqual(generated_artifact_paths_from_status(out), [])


class StatusContainsOnlyRemovableTests(unittest.TestCase):
    def test_true_when_all_paths_are_removable(self) -> None:
        out = '?? build/a\n?? .kato/validation_report.md\n'
        self.assertTrue(
            status_contains_only_removable_artifacts(
                out, ['build'], ['.kato/validation_report.md'],
            )
        )

    def test_false_when_non_removable_path_present(self) -> None:
        out = '?? build/a\n?? src/unexpected.py\n'
        self.assertFalse(
            status_contains_only_removable_artifacts(
                out, ['build'], [],
            )
        )

    def test_true_when_only_bytecode_artifacts_present(self) -> None:
        # A status of nothing-but-__pycache__ counts as "only removable".
        out = '?? pkg/__pycache__/m.cpython-311.pyc\n?? pkg/x.pyc\n'
        self.assertTrue(
            status_contains_only_removable_artifacts(out, [], []),
        )


if __name__ == '__main__':
    unittest.main()

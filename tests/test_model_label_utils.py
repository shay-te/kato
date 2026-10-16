"""Model ids as the operator reads them — one helper for every label."""

from __future__ import annotations

import unittest

from kato_core_lib.helpers.model_label_utils import model_display_label


class ModelDisplayLabelTests(unittest.TestCase):

    def test_pinned_ids_read_as_family_and_version(self) -> None:
        self.assertEqual(model_display_label('claude-opus-4-8'), 'Opus 4.8')
        self.assertEqual(model_display_label(' claude-sonnet-5-5 '), 'Sonnet 5.5')

    def test_the_1m_context_variant_says_so(self) -> None:
        self.assertEqual(model_display_label('claude-opus-5-5[1m]'), 'Opus 5.5 (1M context)')

    def test_an_id_with_no_minor_version_reads_as_family_and_major(self) -> None:
        self.assertEqual(model_display_label('claude-opus-5'), 'Opus 5')
        self.assertEqual(model_display_label('claude-sonnet-5'), 'Sonnet 5')
        self.assertEqual(model_display_label('claude-opus-5[1m]'), 'Opus 5 (1M context)')

    def test_a_snapshot_date_is_dropped_and_never_read_as_a_minor_version(self) -> None:
        self.assertEqual(model_display_label('claude-haiku-4-5-20251001'), 'Haiku 4.5')
        self.assertEqual(model_display_label('claude-opus-4-20250514'), 'Opus 4')
        self.assertEqual(model_display_label('claude-opus-5-5-20260401[1m]'), 'Opus 5.5 (1M context)')

    def test_anything_else_is_shown_as_it_is(self) -> None:
        for model in ('opus', 'gpt-5.2', 'claude-opus', 'claude-opus-5-5[2m]',
                      'claude-opus-5-5-2026', 'claude-opus-5-5-20260'):
            self.assertEqual(model_display_label(model), model)
        self.assertEqual(model_display_label(''), '')
        self.assertEqual(model_display_label(None), '')


if __name__ == '__main__':
    unittest.main()

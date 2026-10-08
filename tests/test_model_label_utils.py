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

    def test_anything_else_is_shown_as_it_is(self) -> None:
        for model in ('opus', 'gpt-5.2', 'claude-opus', 'claude-opus-5-5[2m]'):
            self.assertEqual(model_display_label(model), model)
        self.assertEqual(model_display_label(''), '')
        self.assertEqual(model_display_label(None), '')


if __name__ == '__main__':
    unittest.main()

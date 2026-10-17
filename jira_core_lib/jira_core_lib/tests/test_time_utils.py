import unittest

from jira_core_lib.jira_core_lib.helpers.time_utils import iso_utc


class IsoUtcTests(unittest.TestCase):
    def test_a_jira_utc_timestamp_drops_zero_milliseconds(self) -> None:
        self.assertEqual(iso_utc('2026-10-17T10:00:00.000+0000'), '2026-10-17T10:00:00+00:00')

    def test_another_offset_is_converted_to_utc(self) -> None:
        self.assertEqual(iso_utc('2026-10-17T13:00:00.000+0300'), '2026-10-17T10:00:00+00:00')
        self.assertEqual(iso_utc('2026-10-17T01:00:00.000-0900'), '2026-10-17T10:00:00+00:00')

    def test_milliseconds_are_kept(self) -> None:
        self.assertEqual(iso_utc('2026-10-17T10:00:00.123+0000'), '2026-10-17T10:00:00.123000+00:00')

    def test_other_iso_spellings_are_read_too(self) -> None:
        self.assertEqual(iso_utc('2026-10-17T10:00:00Z'), '2026-10-17T10:00:00+00:00')
        self.assertEqual(iso_utc(' 2026-10-17T10:00:00+00:00 '), '2026-10-17T10:00:00+00:00')

    def test_a_time_without_an_offset_is_taken_as_utc(self) -> None:
        self.assertEqual(iso_utc('2026-10-17T10:00:00'), '2026-10-17T10:00:00+00:00')

    def test_blank_or_unreadable_is_empty(self) -> None:
        for value in (None, '', '   ', 'yesterday', '17/10/2026'):
            with self.subTest(value=value):
                self.assertEqual(iso_utc(value), '')

    def test_converted_times_sort_as_strings(self) -> None:
        # Times from accounts in different time zones compare correctly.
        earlier = iso_utc('2026-10-17T12:59:59.000+0300')  # 09:59:59 UTC
        later = iso_utc('2026-10-17T10:00:00.000+0000')
        self.assertLess(earlier, later)


if __name__ == '__main__':
    unittest.main()

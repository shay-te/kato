"""Paths as Docker reports them, compared with a folder on this computer."""

import unittest

from container_core_lib.container_core_lib.paths import host_path, is_inside


class HostPathTests(unittest.TestCase):
    def test_every_form_docker_desktop_writes_a_windows_path_in_is_one(self) -> None:
        for reported in (
            'C:\\Users\\Dana\\work\\UNA-1', '/run/desktop/mnt/host/c/Users/Dana/work/UNA-1',
            '/host_mnt/c/Users/Dana/work/UNA-1', '/mnt/c/Users/Dana/work/UNA-1', 'c:/users/dana/work/una-1/',
        ):
            self.assertEqual(host_path(reported), 'c:/users/dana/work/una-1', reported)

    def test_a_mac_mount_through_host_mnt_and_plain_paths(self) -> None:
        self.assertEqual(host_path('/host_mnt/Users/dana/work'), '/Users/dana/work')
        self.assertEqual(host_path('/Users//dana/work/'), '/Users/dana/work')  # case kept off Windows
        self.assertEqual(host_path('/mnt/data/x'), '/mnt/data/x')  # not a drive letter
        self.assertEqual(host_path('/'), '/')
        self.assertEqual(host_path(''), '')
        self.assertEqual(host_path(None), '')
        self.assertEqual(host_path('/mnt/c'), 'c:')


class IsInsideTests(unittest.TestCase):
    def test_the_folder_and_anything_under_it(self) -> None:
        self.assertTrue(is_inside('/w/UNA-1', '/w/UNA-1'))
        self.assertTrue(is_inside('/w/UNA-1/api/data', '/w/UNA-1/'))
        self.assertTrue(is_inside('/run/desktop/mnt/host/c/W/UNA-1/api', 'C:\\w\\una-1'))

    def test_a_sibling_with_the_same_start_is_not_inside(self) -> None:
        self.assertFalse(is_inside('/w/UNA-10/api', '/w/UNA-1'))
        self.assertFalse(is_inside('/w', '/w/UNA-1'))
        self.assertFalse(is_inside('', '/w'))
        self.assertFalse(is_inside('/w', ''))


if __name__ == '__main__':
    unittest.main()

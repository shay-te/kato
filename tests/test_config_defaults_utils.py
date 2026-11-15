"""The default each setting falls back to comes from kato's own config files."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kato_core_lib.helpers import config_defaults_utils
from kato_core_lib.helpers.config_defaults_utils import config_defaults


class ConfigDefaultsTests(unittest.TestCase):
    def test_the_real_config_files_give_each_trackers_own_defaults(self) -> None:
        defaults = config_defaults()
        self.assertEqual(defaults['JIRA_DONE_STATE_FIELD'], 'status')
        self.assertEqual(defaults['YOUTRACK_DONE_STATE_FIELD'], 'State')
        self.assertEqual(defaults['JIRA_ISSUE_STATES'], 'To Do,Open')
        self.assertEqual(defaults['GITHUB_ISSUE_STATES'], 'open')
        self.assertEqual(defaults['JIRA_API_TOKEN'], '')  # written as empty

    def test_the_first_file_to_name_a_key_wins_and_an_unreadable_file_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / 'a', Path(tmp) / 'b'
            (first / 'nested').mkdir(parents=True)
            second.mkdir()
            (first / 'nested' / 'x.yaml').write_text('k: ${oc.env:KEY_ONE,"one"}\nm: ${oc.decode:${oc.env:N,"3"}}\n')
            (second / 'y.yaml').write_text('k: ${oc.env:KEY_ONE,"other"}\nj: ${oc.env:KEY_TWO,"two"}\nz: ${oc.env:NO_DEFAULT}\n')
            self.assertEqual(config_defaults((first, second)), {'KEY_ONE': 'one', 'N': '3', 'KEY_TWO': 'two'})
            broken = Path(tmp) / 'c'
            broken.mkdir()
            (broken / 'z.yaml').write_text('k: ${oc.env:SEEN,"x"}')
            with mock.patch.object(config_defaults_utils.Path, 'read_text', side_effect=OSError('unreadable')):
                self.assertEqual(config_defaults((broken,)), {})


if __name__ == '__main__':
    unittest.main()

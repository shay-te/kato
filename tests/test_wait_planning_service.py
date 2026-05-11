import unittest
from unittest.mock import patch

from kato_core_lib.data_layers.service.wait_planning_service import WaitPlanningService
from tests.utils import build_task


class WaitPlanningServicePromptTests(unittest.TestCase):
    def test_planning_prompt_marks_ignored_repositories_out_of_bounds(self) -> None:
        with patch.dict(
            'os.environ',
            {'KATO_IGNORED_REPOSITORY_FOLDERS': 'secret-client'},
        ):
            prompt = WaitPlanningService._build_planning_prompt(build_task())

        self.assertIn('Forbidden repository folders', prompt)
        self.assertIn('- secret-client', prompt)
        self.assertIn('Do not access them with Read, Glob, Grep, Bash', prompt)
        self.assertIn('Execution protocol for forbidden repositories', prompt)
        self.assertIn('DO NOT call any tools', prompt)


if __name__ == '__main__':
    unittest.main()

import os
import unittest
from unittest import mock

from claude_core_lib.claude_core_lib.helpers.sandbox_scope import (
    classify_command_sandbox,
    classify_tool_input_sandbox,
)

CWD = os.path.normpath('/work/UNA-1/admin-backend')
ADD = (os.path.normpath('/work/UNA-1/admin-client'),)


class ClassifyToolInputSandboxTests(unittest.TestCase):
    def test_absolute_path_inside_cwd_is_not_outside(self) -> None:
        outside, path = classify_tool_input_sandbox(
            {'file_path': f'{CWD}/src/app.py'}, CWD, ADD,
        )
        self.assertFalse(outside)
        self.assertEqual(path, '')

    def test_path_inside_an_additional_dir_is_not_outside(self) -> None:
        outside, _ = classify_tool_input_sandbox(
            {'file_path': f'{ADD[0]}/index.js'}, CWD, ADD,
        )
        self.assertFalse(outside)

    def test_absolute_path_outside_all_roots_is_flagged(self) -> None:
        outside, path = classify_tool_input_sandbox(
            {'file_path': '/etc/passwd'}, CWD, ADD,
        )
        self.assertTrue(outside)
        self.assertEqual(path, '/etc/passwd')

    def test_relative_path_resolves_against_cwd(self) -> None:
        outside, _ = classify_tool_input_sandbox(
            {'file_path': 'src/util.py'}, CWD, ADD,
        )
        self.assertFalse(outside)

    def test_dotdot_escape_is_flagged(self) -> None:
        # The actual attack vector: a relative ``../`` that climbs out.
        outside, path = classify_tool_input_sandbox(
            {'file_path': '../../secret.txt'}, CWD, ADD,
        )
        self.assertTrue(outside)
        self.assertEqual(path, '../../secret.txt')

    def test_sibling_task_folder_is_outside(self) -> None:
        # A DIFFERENT task's folder (/work/UNA-2) is still outside — the
        # widened common parent is the task folder /work/UNA-1.
        outside, _ = classify_tool_input_sandbox(
            {'file_path': '/work/UNA-2/admin-backend/x.py'}, CWD, ADD,
        )
        self.assertTrue(outside)

    def test_sibling_repo_under_task_folder_is_inside(self) -> None:
        # The UNA-2727 false positive: a repo that is a sibling of the
        # cwd + add-dirs (same task folder) but is NOT itself a baked
        # root — e.g. a repo cloned after spawn. The common parent of the
        # roots IS the task folder, so it must read as INSIDE.
        outside, path = classify_tool_input_sandbox(
            {'file_path': '/work/UNA-1/ob-love-admin-backend/admin_core_lib/x.py'},
            CWD, ADD,
        )
        self.assertFalse(outside)
        self.assertEqual(path, '')

    def test_single_root_sibling_repo_in_task_folder_is_inside(self) -> None:
        # A comment-/review-driven respawn spawns with cwd ONLY (no
        # --add-dirs). A file in a SIBLING repo of the same task must
        # still read as inside — the task folder (parent of cwd) is a
        # root even with a single cwd. This is the llm-core-lib case.
        outside, _ = classify_tool_input_sandbox(
            {'file_path': '/work/UNA-1/llm-core-lib/llm_core_lib/x.py'},
            '/work/UNA-1/admin-backend', (),
        )
        self.assertFalse(outside)

    def test_sibling_TASK_is_outside_even_with_task_folder_widening(self) -> None:
        # The widened root is THIS task's folder (/work/UNA-1), so a
        # DIFFERENT task's folder (/work/UNA-2) is still outside — real
        # cross-task escapes are not silenced.
        outside, _ = classify_tool_input_sandbox(
            {'file_path': '/work/UNA-2/admin-backend/x.py'},
            '/work/UNA-1/admin-backend', (),
        )
        self.assertTrue(outside)

    def test_shallow_cwd_does_not_widen_to_disk_root(self) -> None:
        # A degenerate shallow cwd must NOT add '/' or a one-segment
        # parent as a root (which would swallow the whole disk).
        outside, _ = classify_tool_input_sandbox(
            {'file_path': '/etc/passwd'}, '/work/repo', (),
        )
        self.assertTrue(outside)

    def test_notebook_path_key_is_inspected(self) -> None:
        outside, path = classify_tool_input_sandbox(
            {'notebook_path': '/tmp/foo.ipynb'}, CWD, ADD,
        )
        self.assertTrue(outside)
        self.assertEqual(path, '/tmp/foo.ipynb')

    def test_bare_command_without_path_is_not_flagged(self) -> None:
        # A Bash command has no path argument we can trust → not outside,
        # so remembered git/ls approvals keep working.
        outside, path = classify_tool_input_sandbox(
            {'command': 'rm -rf /etc'}, CWD, ADD,
        )
        self.assertFalse(outside)
        self.assertEqual(path, '')

    def test_no_roots_configured_never_flags(self) -> None:
        outside, _ = classify_tool_input_sandbox(
            {'file_path': '/etc/passwd'}, '', (),
        )
        self.assertFalse(outside)

    def test_non_dict_input_is_safe(self) -> None:
        self.assertEqual(
            classify_tool_input_sandbox('nope', CWD, ADD), (False, ''),
        )

    def test_prefix_sibling_task_is_not_treated_as_inside(self) -> None:
        # ``/work/UNA-12`` string-prefix-matches the task folder
        # ``/work/UNA-1`` but is a DIFFERENT task — the separator guard
        # keeps it outside (no naive ``startswith`` false-inside).
        outside, _ = classify_tool_input_sandbox(
            {'file_path': '/work/UNA-12/admin-backend/x.py'},
            '/work/UNA-1/admin-backend', (),
        )
        self.assertTrue(outside)



    def test_cwd_root_itself_is_inside(self) -> None:
        outside, _ = classify_tool_input_sandbox(
            {'path': CWD}, CWD, ADD,
        )
        self.assertFalse(outside)

    def test_allowed_path_outside_sandbox_is_not_flagged(self) -> None:
        # kato's configured lessons.md / architecture.md live outside the task
        # folder but the agent is MEANT to touch them — an exact allow-list
        # match must NOT trip the out-of-sandbox warning.
        lessons = os.path.normpath('/Users/x/Desktop/dev_kato/lessons.md')
        outside, _ = classify_tool_input_sandbox(
            {'file_path': lessons}, CWD, ADD, (lessons,),
        )
        self.assertFalse(outside)

    def test_other_outside_path_still_flagged_with_allowlist(self) -> None:
        # The allow-list is exact: a DIFFERENT outside file still warns.
        lessons = os.path.normpath('/Users/x/Desktop/dev_kato/lessons.md')
        outside, offending = classify_tool_input_sandbox(
            {'file_path': '/etc/passwd'}, CWD, ADD, (lessons,),
        )
        self.assertTrue(outside)
        self.assertEqual(offending, '/etc/passwd')


class ClassifyCommandSandboxTests(unittest.TestCase):
    # User-space task folder so the within-roots check is exercised the same
    # way it runs in production (kato workspaces live under the home tree).
    UCWD = '/Users/dev/.kato/workspaces/UNA-1/repo'

    def test_grep_into_another_repo_is_flagged(self) -> None:
        outside, offending = classify_command_sandbox(
            'grep -rn secret /Users/dev/Desktop/dev/other-repo/src', self.UCWD,
        )
        self.assertTrue(outside)
        self.assertEqual(offending, '/Users/dev/Desktop/dev/other-repo/src')

    def test_absolute_path_buried_in_a_quoted_python_string_is_flagged(self) -> None:
        # The exact reported miss: the path is inside ``open('…')`` in a
        # ``python3 -c`` string, not a space-separated token.
        cmd = (
            "python3 -c \"import ast; "
            "ast.parse(open('/Users/dev/Desktop/dev/ob-love-devops-local/"
            "update_server.py').read()); print('OK')\""
        )
        outside, offending = classify_command_sandbox(cmd, self.UCWD)
        self.assertTrue(outside)
        self.assertEqual(
            offending,
            '/Users/dev/Desktop/dev/ob-love-devops-local/update_server.py',
        )

    def test_reading_a_home_dotfile_is_flagged(self) -> None:
        with mock.patch.dict(os.environ, {'HOME': '/Users/dev'}):
            outside, offending = classify_command_sandbox(
                'cat ~/.ssh/id_rsa', self.UCWD,
            )
        self.assertTrue(outside)
        self.assertEqual(offending, '~/.ssh/id_rsa')

    def test_cd_into_task_folder_then_relative_args_is_inside(self) -> None:
        cmd = 'cd /Users/dev/.kato/workspaces/UNA-1/repo && grep -rn x src/main'
        outside, _ = classify_command_sandbox(cmd, self.UCWD)
        self.assertFalse(outside)

    def test_relative_paths_and_globs_are_not_flagged(self) -> None:
        # ``*/main/*`` contains a ``/main/*`` substring but isn't user-space,
        # so it must NOT false-alarm (the classic command-scan noise).
        outside, _ = classify_command_sandbox(
            'find . -name "*.java" -path "*/main/*"', self.UCWD,
        )
        self.assertFalse(outside)

    def test_system_paths_and_urls_are_ignored(self) -> None:
        for cmd in (
            'cat /etc/hosts', 'ls /usr/local/bin', 'grep x /var/log/y',
            'curl https://example.com/api/data',
        ):
            outside, _ = classify_command_sandbox(cmd, self.UCWD)
            self.assertFalse(outside, cmd)

    def test_allowed_path_in_command_is_exempt(self) -> None:
        lessons = os.path.normpath('/Users/dev/.kato/lessons.md')
        outside, _ = classify_command_sandbox(
            f'cat {lessons}', self.UCWD, (), (lessons,),
        )
        self.assertFalse(outside)

    def test_empty_command_is_inside(self) -> None:
        self.assertEqual(classify_command_sandbox('', self.UCWD), (False, ''))


if __name__ == '__main__':
    unittest.main()

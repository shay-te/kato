import os
import unittest

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

    def test_grep_into_another_repo_is_flagged(self) -> None:
        outside, offending = classify_command_sandbox(
            'grep -rn secret /Users/x/Desktop/dev/other-repo/src', CWD, ADD,
        )
        self.assertTrue(outside)
        self.assertEqual(offending, '/Users/x/Desktop/dev/other-repo/src')

    def test_reading_home_dotfile_is_flagged(self) -> None:
        outside, offending = classify_command_sandbox(
            'cat ~/.ssh/id_rsa', CWD, ADD,
        )
        self.assertTrue(outside)
        self.assertEqual(offending, '~/.ssh/id_rsa')

    def test_cd_into_task_folder_then_relative_args_is_inside(self) -> None:
        # The classic prefix: cd into the task workspace, then relative paths.
        cmd = (
            'cd /work/UNA-1 && grep -rnE "x" admin-backend/src admin-client/src'
        )
        outside, _ = classify_command_sandbox(cmd, CWD, ADD)
        self.assertFalse(outside)

    def test_relative_paths_are_not_flagged(self) -> None:
        outside, _ = classify_command_sandbox(
            'find . -name "*.java" -path "*/main/*"', CWD, ADD,
        )
        self.assertFalse(outside)

    def test_system_dirs_are_exempt(self) -> None:
        # Reading a binary/config under a system tree is low-signal noise.
        for cmd in ('cat /etc/hosts', 'ls /usr/local/bin', 'grep x /var/log/y'):
            outside, _ = classify_command_sandbox(cmd, CWD, ADD)
            self.assertFalse(outside, cmd)

    def test_absolute_program_path_is_not_the_offender(self) -> None:
        # The program token itself (even an absolute one) is skipped.
        outside, _ = classify_command_sandbox(
            '/usr/local/bin/python script.py', CWD, ADD,
        )
        self.assertFalse(outside)

    def test_allowed_path_in_command_is_exempt(self) -> None:
        lessons = os.path.normpath('/Users/x/Desktop/dev_kato/lessons.md')
        outside, _ = classify_command_sandbox(
            f'cat {lessons}', CWD, ADD, (lessons,),
        )
        self.assertFalse(outside)

    def test_empty_command_is_inside(self) -> None:
        self.assertEqual(classify_command_sandbox('', CWD, ADD), (False, ''))


if __name__ == '__main__':
    unittest.main()

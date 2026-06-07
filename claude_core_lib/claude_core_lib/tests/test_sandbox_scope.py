import os
import unittest
from unittest import mock

from claude_core_lib.claude_core_lib.helpers.sandbox_scope import (
    classify_command_escape,
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


class ClassifyCommandSandboxAdversarialTests(unittest.TestCase):
    """Super-challenging commands: forbidden home-tree paths buried deep in
    quotes / JSON / subshells / long text MUST be caught, while in-task paths,
    the allow-list, system paths, URLs and glob/regex fragments MUST NOT
    false-alarm."""

    TASK = '/Users/dev/.kato/workspaces/UNA-1'
    CWD = TASK + '/admin-backend'
    ADD = (TASK + '/admin-client',)  # sibling repo of the SAME task
    ALLOWED = (
        '/Users/dev/.kato/lessons.md',
        '/Users/dev/.kato/architecture.md',
    )

    def _run(self, cmd):
        return classify_command_sandbox(cmd, self.CWD, self.ADD, self.ALLOWED)

    def assertFlagged(self, cmd, expected_offending):
        outside, offending = self._run(cmd)
        self.assertTrue(outside, f'expected FLAG but was clean:\n  {cmd}')
        self.assertEqual(offending, expected_offending)

    def assertClean(self, cmd):
        outside, offending = self._run(cmd)
        self.assertFalse(
            outside, f'expected CLEAN but flagged {offending!r}:\n  {cmd}',
        )

    # ---- MUST FLAG: a forbidden home-tree path, however buried -------------

    def test_buried_in_quoted_python_open(self):
        self.assertFlagged(
            "python3 -c \"import ast; ast.parse(open("
            "'/Users/dev/Desktop/dev/ob-love/update_server.py').read())\"",
            '/Users/dev/Desktop/dev/ob-love/update_server.py',
        )

    def test_buried_in_a_wall_of_text(self):
        cmd = (
            'echo "kicking off a long multi-stage diagnostic that scans every '
            'module and service and eventually slurps the production secrets '
            'living at" /Users/dev/Desktop/secrets/prod.env " and then keeps '
            'going for a while talking about totally unrelated things"'
        )
        self.assertFlagged(cmd, '/Users/dev/Desktop/secrets/prod.env')

    def test_inside_a_json_request_body(self):
        self.assertFlagged(
            'curl -s -XPOST http://localhost:8080/run '
            '-d \'{"script":"/Users/dev/Desktop/dev/other-repo/deploy.sh",'
            '"env":"prod"}\'',
            '/Users/dev/Desktop/dev/other-repo/deploy.sh',
        )

    def test_after_an_equals_flag(self):
        self.assertFlagged(
            'mytool --verbose --config=/Users/dev/Desktop/other/config.yaml',
            '/Users/dev/Desktop/other/config.yaml',
        )

    def test_nested_double_quotes_in_node_eval(self):
        self.assertFlagged(
            'bash -c "node -e \\"require(\'fs\')'
            '.readFileSync(\'/Users/dev/Desktop/other/key.pem\')\\""',
            '/Users/dev/Desktop/other/key.pem',
        )

    def test_inside_a_command_substitution(self):
        self.assertFlagged(
            'cat $(ls /Users/dev/Desktop/other/dropbox)',
            '/Users/dev/Desktop/other/dropbox',
        )

    def test_sibling_task_workspace_is_outside(self):
        # A DIFFERENT task's clone under the same workspaces root is still out.
        self.assertFlagged(
            'cat /Users/dev/.kato/workspaces/UNA-2/repo/.env',
            '/Users/dev/.kato/workspaces/UNA-2/repo/.env',
        )

    def test_prefix_similar_task_is_outside(self):
        # ``UNA-12`` string-prefixes ``UNA-1`` but is a different dir — the
        # separator guard must keep it OUT (no naive startswith false-inside).
        self.assertFlagged(
            'grep token /Users/dev/.kato/workspaces/UNA-12/repo/secrets.txt',
            '/Users/dev/.kato/workspaces/UNA-12/repo/secrets.txt',
        )

    def test_dotdot_traversal_into_a_sibling_task(self):
        outside, _ = self._run(
            'cat /Users/dev/.kato/workspaces/UNA-1/../UNA-2/secret',
        )
        self.assertTrue(outside)

    def test_home_dotfile_buried_with_a_scratch_path(self):
        with mock.patch.dict(os.environ, {'HOME': '/Users/dev'}):
            # ``/tmp/out.tgz`` is scratch (ignored); ``~/.aws/credentials`` is
            # the real escape and must be caught.
            self.assertFlagged(
                'tar czf /tmp/out.tgz ~/.aws/credentials',
                '~/.aws/credentials',
            )

    def test_forbidden_wins_over_inside_and_allowed_paths(self):
        # Same command touches an in-task file, the allow-listed lessons.md,
        # AND a forbidden path — the forbidden one must be the offender.
        cmd = (
            'python3 merge.py '
            '/Users/dev/.kato/workspaces/UNA-1/admin-backend/in.json '
            '/Users/dev/.kato/lessons.md '
            '/Users/dev/Desktop/other/leak.json'
        )
        self.assertFlagged(cmd, '/Users/dev/Desktop/other/leak.json')

    # ---- MUST NOT FLAG: in-task / allow-list / system / URL / glob --------

    def test_in_task_path_buried_in_python_open_is_clean(self):
        self.assertClean(
            "python3 -c \"open('/Users/dev/.kato/workspaces/UNA-1/"
            "admin-backend/app/config.py').read()\"",
        )

    def test_sibling_repo_of_same_task_is_clean(self):
        self.assertClean(
            'cat /Users/dev/.kato/workspaces/UNA-1/admin-client/pom.xml',
        )

    def test_dotdot_staying_inside_the_task_is_clean(self):
        self.assertClean(
            'cat /Users/dev/.kato/workspaces/UNA-1/admin-backend/../'
            'admin-client/src/Main.java',
        )

    def test_allowlisted_lessons_buried_is_clean(self):
        self.assertClean(
            "python3 -c \"print(open('/Users/dev/.kato/lessons.md').read())\"",
        )

    def test_system_path_buried_is_clean(self):
        self.assertClean('python3 -c "print(open(\'/etc/hosts\').read())"')

    def test_url_with_userlike_path_is_clean(self):
        # ``/Users/dev`` appears in the URL PATH, but it's behind ``//host`` so
        # it never reads as a home-tree filesystem path.
        self.assertClean('curl https://cdn.example.com/Users/dev/avatar.png')

    def test_glob_and_regex_fragments_are_clean(self):
        self.assertClean('find . -path "*/main/*" -name "*.java"')
        self.assertClean("sed -i 's|/var/log/app|/tmp/app|g' run.log")

    def test_relative_paths_only_is_clean(self):
        self.assertClean('grep -rn TODO src/ test/ ./scripts/build.sh')

    # ---- obfuscation hardening: must still FLAG --------------------------

    def test_quote_split_path_is_flagged(self):
        # ``/Use"rs"/dev`` tries to dodge a substring match by splitting the
        # path with quotes — deobfuscation flattens it.
        self.assertFlagged(
            'cat /Use"rs"/dev/Desktop/other/sec"ret".txt',
            '/Users/dev/Desktop/other/secret.txt',
        )

    def test_backslash_escaped_path_is_flagged(self):
        self.assertFlagged(
            'cat \\/Users\\/dev\\/Desktop\\/other\\/secret',
            '/Users/dev/Desktop/other/secret',
        )

    def test_home_variable_indirection_is_flagged(self):
        self.assertFlagged('cat $HOME/.ssh/id_rsa', '~/.ssh/id_rsa')
        self.assertFlagged('cat ${HOME}/.aws/credentials', '~/.aws/credentials')

    def test_relative_dotdot_climb_out_is_flagged(self):
        # cwd is …/UNA-1/admin-backend; ``../../UNA-2/repo`` climbs into a
        # different task and must be caught even though it's relative.
        outside, _ = self._run('cat ../../UNA-2/repo/.env')
        self.assertTrue(outside)

    def test_relative_dotdot_buried_in_quotes_is_flagged(self):
        outside, _ = self._run(
            "python3 -c \"print(open('../../../etc/shadow').read())\"",
        )
        self.assertTrue(outside)

    # ---- obfuscation hardening: must still stay CLEAN --------------------

    def test_relative_dotdot_staying_in_task_is_clean(self):
        # …/admin-backend/../admin-client resolves to a sibling repo of the
        # SAME task (an additional_dir) — inside, not an escape.
        self.assertClean('cat ../admin-client/src/Main.java')

    def test_home_var_into_the_task_is_clean(self):
        # If HOME *is* the workspace parent, $HOME-relative stays inside.
        with mock.patch.dict(os.environ, {'HOME': self.TASK}):
            self.assertClean('cat $HOME/admin-backend/app/x.py')


class ClassifyCommandEscapeTests(unittest.TestCase):
    """Container-runtime / privilege escapes reach the host around any path
    sandbox, so they must be flagged regardless of which paths they name."""

    def test_docker_run_is_flagged(self):
        escapes, program = classify_command_escape(
            'docker run --rm -v /tmp/x:/data alpine sh -c "cat /data/f"',
        )
        self.assertTrue(escapes)
        self.assertEqual(program, 'docker')

    def test_the_reported_jfr_docker_command_is_flagged(self):
        cmd = (
            'mkdir -p /tmp/jfr-validation && rm -f /tmp/jfr-validation/*.jfr '
            '&& docker run --rm -v /tmp/jfr-validation:/var/lib/kafka-streams '
            '--entrypoint sh eclipse-temurin:17-jre-jammy -c '
            "'exec java -version' 2>&1 | tail -20"
        )
        escapes, program = classify_command_escape(cmd)
        self.assertTrue(escapes)
        self.assertEqual(program, 'docker')

    def test_escape_after_cd_and_env_and_sudo(self):
        self.assertEqual(
            classify_command_escape('cd /tmp && FOO=bar docker compose up')[1],
            'docker',
        )
        self.assertEqual(
            classify_command_escape('sudo rm -rf /')[1], 'sudo',
        )
        self.assertEqual(
            classify_command_escape('cat x.txt | sudo tee /etc/hosts')[1],
            'sudo',
        )

    def test_absolute_runtime_path_is_still_an_escape(self):
        self.assertEqual(
            classify_command_escape('/usr/local/bin/docker ps')[1], 'docker',
        )

    def test_other_runtimes_and_namespace_tools(self):
        for cmd, prog in (
            ('podman run alpine', 'podman'),
            ('nerdctl ps', 'nerdctl'),
            ('kubectl get pods', 'kubectl'),
            ('chroot /mnt /bin/sh', 'chroot'),
            ('nsenter -t 1 -m', 'nsenter'),
            ('docker-compose up -d', 'docker-compose'),
        ):
            self.assertEqual(classify_command_escape(cmd)[1], prog, cmd)

    def test_ordinary_commands_are_not_escapes(self):
        for cmd in (
            'git status', 'ls -la', 'mvn -B verify', 'grep -rn TODO src',
            'echo "use docker to deploy"',   # mentions docker, doesn't run it
            'cat Dockerfile',                 # reads a file named like it
            'python3 dockerize.py',           # program is python3
        ):
            escapes, _ = classify_command_escape(cmd)
            self.assertFalse(escapes, cmd)


if __name__ == '__main__':
    unittest.main()

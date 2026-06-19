"""End-to-end flow tests for :class:`CodexCliClient` — A-Z scenarios.

Each test class represents one named flow and drives the client's real
public surface (``validate_connection`` → ``implement_task`` →
``fix_review_comment`` → ``investigate`` / ``test_task``) directly,
top to bottom, with only the lowest-level boundary mocked:
``shutil.which`` for binary discovery and ``subprocess.run`` for the
``codex exec`` spawn. Nothing inside the client is patched, so the
full argv construction, JSONL parsing, ``--output-last-message`` file
recovery, and result assembly logic all run for real.

The client is constructed directly via
``from codex_core_lib.codex_core_lib.cli_client import CodexCliClient``
— this lib is standalone and does NOT depend on any backend-selector
layer above it.

Codex's output channel is split between JSONL events (stdout) and the
``--output-last-message`` file, so the fake ``subprocess.run`` writes
BOTH the same way the real CLI does: it returns the JSONL stream on
stdout and writes the final reply to the path passed after ``-o``.
"""

from __future__ import annotations

import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_core_lib.agent_core_lib.data.fields import ImplementationFields
from codex_core_lib.codex_core_lib.cli_client import CodexCliClient
from provider_client_base.provider_client_base.data.review_comment import ReviewComment

_RUN_PATH = 'codex_core_lib.codex_core_lib.cli_client.subprocess.run'
_WHICH_PATH = 'codex_core_lib.codex_core_lib.cli_client.shutil.which'


# ---------------------------------------------------------------------------
# Shared doubles — mirror the conventions in test_codex_cli_client.py exactly.
# ---------------------------------------------------------------------------

def _completed(stdout: str = '', stderr: str = '', returncode: int = 0):
    """The completed-process double codex's parser consumes."""
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def _success_jsonl(thread_id: str = 'thread-1', text: str = 'done editing') -> str:
    """A real-shaped ``codex exec --json`` success stream.

    Mirrors the bytes captured from codex-cli 0.132.0: ``thread.started``
    carries the session id under ``thread_id``, the agent reply is nested
    under an ``item.completed`` agent-message event, and ``turn.completed``
    closes the turn with usage stats.
    """
    return (
        f'{{"type":"thread.started","thread_id":"{thread_id}"}}\n'
        '{"type":"turn.started"}\n'
        '{"type":"item.completed","item":{"id":"item_0",'
        f'"type":"agent_message","text":"{text}"}}}}\n'
        '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":2}}\n'
    )


def _fake_codex_run(*, jsonl: str = '', last_message: str = '',
                    stderr: str = '', returncode: int = 0):
    """Build a ``subprocess.run`` replacement that writes ``last_message``
    to the ``--output-last-message`` (``-o``) path and returns ``jsonl``
    on stdout — exactly what the real codex CLI does."""

    def fake_run(command, **kwargs):
        try:
            idx = command.index('-o')
            path = command[idx + 1]
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write(last_message)
        except (ValueError, IndexError, OSError):
            pass
        return _completed(stdout=jsonl, stderr=stderr, returncode=returncode)

    return fake_run


def _recording_codex_run(seen: list, *, last_message: str = 'ok',
                         jsonl: str = '', returncode: int = 0):
    """Like ``_fake_codex_run`` but appends each argv to ``seen`` so a
    flow can assert the exact command line codex was invoked with."""

    def fake_run(command, **kwargs):
        seen.append(list(command))
        try:
            idx = command.index('-o')
            with open(command[idx + 1], 'w', encoding='utf-8') as handle:
                handle.write(last_message)
        except (ValueError, IndexError, OSError):
            pass
        return _completed(stdout=jsonl, returncode=returncode)

    return fake_run


def _task(task_id: str = 'PROJ-1') -> SimpleNamespace:
    return SimpleNamespace(
        id=task_id,
        summary='Add feature',
        description='Detailed description of the change.',
        branch_name=f'feature/{task_id.lower()}',
        repository_branches={},
        repositories=[],
    )


def _review_comment(
    *,
    comment_id: str = 'c-1',
    author: str = 'reviewer',
    body: str = 'please rename foo to bar',
) -> ReviewComment:
    return ReviewComment(
        pull_request_id='pr-7',
        comment_id=comment_id,
        author=author,
        body=body,
        file_path='src/example.py',
        line_number=42,
        line_type='ADDED',
        commit_sha='abc1234',
    )


def _client(**kwargs) -> CodexCliClient:
    # ``model_smoke_test_enabled=False`` keeps validate_connection a single
    # version probe; the smoke-test path is covered explicitly in F2.
    kwargs.setdefault('binary', 'codex')
    kwargs.setdefault('model', 'gpt-5-codex')
    kwargs.setdefault('model_smoke_test_enabled', False)
    return CodexCliClient(**kwargs)


# ---------------------------------------------------------------------------
# F1 — Primary workflow A-Z: validate -> implement -> fix review -> investigate
# ---------------------------------------------------------------------------
class F1_PrimaryWorkflowEndToEnd(unittest.TestCase):
    """The full happy path a host driver walks through for one task:
    confirm the binary is reachable, implement the task, address a review
    comment on the resulting branch, then run a read-only investigation —
    asserting both the argv and the parsed result shape at every step."""

    def test_flow(self):
        client = _client(repository_root_path='/repos')

        # --- A. validate_connection: binary found + version probe OK ---
        with patch.object(CodexCliClient, '_running_inside_docker', return_value=False), \
             patch(_WHICH_PATH, return_value='/usr/bin/codex'), \
             patch(_RUN_PATH, return_value=_completed(stdout='codex-cli 0.132.0\n', returncode=0)):
            client.validate_connection()
        self.assertEqual(client._binary_path, '/usr/bin/codex')

        # --- B. implement_task: assert the `codex exec` argv, then parse ---
        seen: list[list[str]] = []
        with patch(_RUN_PATH, side_effect=_recording_codex_run(
                seen, last_message='implementation complete',
                jsonl=_success_jsonl(thread_id='sess-impl', text='ignored-prefer-file'))):
            impl_result = client.implement_task(_task())

        self.assertTrue(seen, 'codex exec was not invoked')
        argv = seen[0]
        # ``validate_connection`` above resolved the binary to its absolute
        # path, so argv[0] is that resolved path; what matters is that the
        # ``exec`` subcommand follows it.
        self.assertEqual(argv[0], '/usr/bin/codex')
        self.assertEqual(argv[1], 'exec')
        self.assertIn('--json', argv)
        self.assertIn('--skip-git-repo-check', argv)
        # Safe-mode default sandbox on a fresh (non-resume) exec.
        self.assertIn('--sandbox', argv)
        self.assertEqual(argv[argv.index('--sandbox') + 1], 'workspace-write')
        self.assertNotIn('--ask-for-approval', argv)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', argv)
        self.assertEqual(argv[argv.index('-m') + 1], 'gpt-5-codex')
        # Fresh exec is NOT a resume.
        self.assertNotIn('resume', argv[:4])

        # Result: message comes from the -o file; session id from thread_id.
        self.assertTrue(impl_result[ImplementationFields.SUCCESS])
        self.assertEqual(impl_result[ImplementationFields.MESSAGE], 'implementation complete')
        self.assertEqual(impl_result[ImplementationFields.AGENT_SESSION_ID], 'sess-impl')

        # --- C. fix_review_comment on the same branch ---
        with patch(_RUN_PATH, side_effect=_fake_codex_run(
                jsonl=_success_jsonl(thread_id='sess-review'),
                last_message='addressed the comment')):
            review_result = client.fix_review_comment(
                _review_comment(),
                branch_name='feature/proj-1',
                task_id='PROJ-1',
                task_summary='Add feature',
            )
        self.assertTrue(review_result[ImplementationFields.SUCCESS])
        self.assertEqual(review_result[ImplementationFields.MESSAGE], 'addressed the comment')

        # --- D. investigate: read-only triage turn returns plain text ---
        triage_seen: list[list[str]] = []
        with patch(_RUN_PATH, side_effect=_recording_codex_run(
                triage_seen, last_message='priority: high')):
            verdict = client.investigate('Classify this ticket', cwd='/repos/example')
        self.assertEqual(verdict, 'priority: high')
        triage_argv = triage_seen[0]
        self.assertIn('--sandbox', triage_argv)
        self.assertEqual(triage_argv[triage_argv.index('--sandbox') + 1], 'read-only')

        # --- E. test_task: separate validation spawn, parsed result ---
        with patch(_RUN_PATH, side_effect=_fake_codex_run(
                jsonl=_success_jsonl(thread_id='sess-test'),
                last_message='tests green')):
            test_result = client.test_task(_task())
        self.assertTrue(test_result[ImplementationFields.SUCCESS])
        self.assertEqual(test_result[ImplementationFields.MESSAGE], 'tests green')


# ---------------------------------------------------------------------------
# F2 — validate_connection runs the model smoke test when enabled
# ---------------------------------------------------------------------------
class F2_ValidateConnectionWithSmokeTest(unittest.TestCase):
    """Flow: with the smoke-test flag on, validate_connection probes the
    version AND spawns a throwaway model-access turn before returning."""

    def test_smoke_test_spawn_runs(self):
        client = _client(model_smoke_test_enabled=True)
        spawns: list[list[str]] = []

        def fake_run(command, **kwargs):
            spawns.append(list(command))
            # First call is `--version`; the smoke-test call is a real
            # `codex exec`. Both must report success.
            if '--version' in command:
                return _completed(stdout='codex-cli 0.132.0\n', returncode=0)
            return _completed(stdout=_success_jsonl(text='ok'), returncode=0)

        with patch.object(CodexCliClient, '_running_inside_docker', return_value=False), \
             patch(_WHICH_PATH, return_value='/usr/bin/codex'), \
             patch(_RUN_PATH, side_effect=fake_run):
            client.validate_connection()

        # Two spawns: the version probe and the smoke-test exec. Both use
        # the binary path resolved by ``shutil.which`` above.
        self.assertEqual(len(spawns), 2)
        self.assertIn('--version', spawns[0])
        self.assertEqual(spawns[1][0], '/usr/bin/codex')
        self.assertEqual(spawns[1][1], 'exec')

    def test_missing_binary_raises_install_hint(self):
        client = _client(binary='nope-binary')
        with patch.object(CodexCliClient, '_running_inside_docker', return_value=False), \
             patch(_WHICH_PATH, return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                client.validate_connection()
        msg = str(ctx.exception)
        self.assertIn('nope-binary', msg)
        self.assertIn('npm install', msg)


# ---------------------------------------------------------------------------
# F3 — Resume uses the `resume <id>` subcommand form, not a --resume flag
# ---------------------------------------------------------------------------
class F3_ResumeUsesSubcommandForm(unittest.TestCase):
    """Flow: handing implement_task an existing session id must produce a
    ``codex exec resume <id>`` argv — codex resumes via a sub-subcommand,
    not a ``--resume`` flag — and must drop the resume-incompatible flags."""

    def test_flow(self):
        client = _client()
        seen: list[list[str]] = []

        with patch(_RUN_PATH, side_effect=_recording_codex_run(
                seen, last_message='resumed work',
                jsonl=_success_jsonl(thread_id='resume-me'))):
            result = client.implement_task(_task(), agent_session_id='resume-me')

        self.assertTrue(seen)
        argv = seen[0]
        # Subcommand form: `codex exec resume <id>`, NOT `--resume <id>`.
        self.assertEqual(argv[:4], ['codex', 'exec', 'resume', 'resume-me'])
        self.assertNotIn('--resume', argv)
        # Resume inherits sandbox / cwd / add-dir from the original spawn,
        # so those flags must NOT be re-emitted.
        self.assertNotIn('--sandbox', argv)
        self.assertNotIn('-C', argv)
        self.assertNotIn('--add-dir', argv)
        # Flags that resume DOES accept still pass through.
        self.assertIn('--json', argv)
        self.assertIn('-o', argv)
        self.assertEqual(result[ImplementationFields.MESSAGE], 'resumed work')


# ---------------------------------------------------------------------------
# F4 — A JSONL error event surfaces as a clear RuntimeError
# ---------------------------------------------------------------------------
class F4_JsonlErrorEventRaises(unittest.TestCase):
    """Flow: the CLI may exit 0 yet stream an error event; the parser must
    flip the failure flag and raise a RuntimeError carrying the message."""

    def test_top_level_error_event(self):
        client = _client()
        jsonl = '{"type":"error","message":"model rejected the request"}\n'
        with patch(_RUN_PATH, side_effect=_fake_codex_run(jsonl=jsonl, returncode=0)):
            with self.assertRaises(RuntimeError) as ctx:
                client.implement_task(_task())
        self.assertIn('model rejected the request', str(ctx.exception))

    def test_nonzero_exit_propagates_stderr(self):
        client = _client()
        with patch(_RUN_PATH, side_effect=_fake_codex_run(
                stderr='authentication required', returncode=1)):
            with self.assertRaises(RuntimeError) as ctx:
                client.implement_task(_task())
        self.assertIn('authentication required', str(ctx.exception))

    def test_timeout_raises_timeout_error(self):
        client = _client(timeout_seconds=60)
        with patch(_RUN_PATH, side_effect=subprocess.TimeoutExpired(cmd='codex', timeout=60)):
            with self.assertRaises(TimeoutError):
                client.implement_task(_task())


# ---------------------------------------------------------------------------
# F5 — Answer-mode review reply returns text without claiming an edit
# ---------------------------------------------------------------------------
class F5_ReviewAnswerMode(unittest.TestCase):
    """Flow: a reviewer question is answered (mode='answer') — the spawn
    returns the explanation as the message with the same success shape as
    fix mode, and a batch of comments routes through the same path."""

    def test_single_answer(self):
        client = _client()
        with patch(_RUN_PATH, side_effect=_fake_codex_run(
                jsonl=_success_jsonl(thread_id='sess-q'),
                last_message='it works because the index is 0-based')):
            result = client.fix_review_comments(
                [_review_comment(body='why is this off-by-one?')],
                'main', task_id='PROJ-1', mode='answer',
            )
        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(
            result[ImplementationFields.MESSAGE],
            'it works because the index is 0-based',
        )

    def test_batch_fix(self):
        client = _client()
        comments = [
            _review_comment(comment_id='c-1', body='first nit'),
            _review_comment(comment_id='c-2', body='second nit'),
        ]
        with patch(_RUN_PATH, side_effect=_fake_codex_run(
                jsonl=_success_jsonl(), last_message='both addressed')):
            result = client.fix_review_comments(comments, 'main', task_id='PROJ-1')
        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(result[ImplementationFields.MESSAGE], 'both addressed')

    def test_empty_comment_list_rejected(self):
        with self.assertRaises(ValueError):
            _client().fix_review_comments([], 'main')


if __name__ == '__main__':
    unittest.main()

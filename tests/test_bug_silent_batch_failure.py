"""Adversarial regression test for kato bug:
``_process_review_comment_batch_best_effort`` silently swallows
exceptions from the batch method with NO logging.

Surface:

    try:
        result = batch_method(comments)
    except Exception:
        return []   # <- silent swallow, no logger, no diagnostics

Why this matters:
    - A transient batch failure (network timeout, rate limit, server
      hiccup) is INVISIBLE to the operator.
    - The scan loop returns ``[]`` so the caller assumes "nothing to
      process this tick" — no retry signal, no diagnostic.
    - The same comments stay pending, are re-submitted next tick,
      and if the failure is persistent the operator sees no
      "review-fix failing" signal anywhere.

Symmetric to the kato false-success guard (which DOES route through a
failure handler so operators see "agent produced no commits"): when a
batch fails, the operator should also see a log entry. The bare
``except: return []`` violates that.

This test verifies the exception is at minimum LOGGED before being
swallowed, so an operator triaging "no reviews are happening" has a
trail to follow.
"""

from __future__ import annotations

import logging
import unittest
from unittest.mock import MagicMock

from kato_core_lib.jobs.process_assigned_tasks import (
    _process_review_comment_batch_best_effort,
)


class BugSilentBatchFailureTests(unittest.TestCase):

    def test_batch_exception_is_logged_not_silently_swallowed(self) -> None:
        # When the batch method raises, the caller MUST see a log
        # entry. Otherwise the operator has no idea why their review
        # comments aren't being processed.
        service = MagicMock()
        # Important: logger attribute on service should receive the
        # exception. Either explicitly or via the module's logger.
        boom = RuntimeError('simulated transient network failure')
        service.process_review_comment_batch.side_effect = boom

        # Capture logs at WARNING/ERROR level from the workflow logger
        # (kato uses a non-propagating workflow logger; root won't see
        # the entries).
        with self.assertLogs(
            'kato.workflow', level=logging.WARNING,
        ) as captured:
            result = _process_review_comment_batch_best_effort(
                service, [MagicMock()],
            )

        # Returned empty (graceful degradation) — but the exception
        # MUST have been logged.
        self.assertEqual(result, [])
        # Log entries should include the exception.
        log_text = '\n'.join(captured.output)
        self.assertIn(
            'simulated transient network failure', log_text,
            f'batch exception was swallowed with NO log entry. '
            f'Captured logs:\n{log_text}\n'
            f'Operator triaging "review comments not being processed" '
            f'has no trail to follow.',
        )


if __name__ == '__main__':
    unittest.main()

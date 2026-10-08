"""What the loop carries from one round to the next, read back from the rounds.

The loop judges each review by what came before it: which blocking issues the
last fix claimed (so a repeat counts as a missed fix), how many fixes each
issue has survived (two and the loop is stuck), the id an issue was first sent
under, whether the next review is a clean-room sweep, and the exact change the
tests last passed on.

All of it is DERIVED from the saved rounds — never kept anywhere else. The
running loop reads it here at the start of every round, and so does a loop
resumed after a stop, a failure or a restart; one derivation is what makes a
resumed loop judge its next review exactly as the uninterrupted one would have.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from review_loop_core_lib.review_loop_core_lib.data.state import ReviewRound
from review_loop_core_lib.review_loop_core_lib.verdict import count_missed_fixes


@dataclass
class LoopMemory(object):
    """The loop's memory going into a round (see the module docstring)."""

    # The blocking issues the last fix claimed fixed or left unanswered; None
    # when no fix came right before (the first round, or after a clean one).
    previous: frozenset[str] | None = None
    # Per issue (fingerprint): how many fixes it has survived.
    missed: dict[str, int] = field(default_factory=dict)
    # Per issue: the id it was first sent to the chat under.
    first_ids: dict[str, str] = field(default_factory=dict)
    # The next review is a clean-room sweep: told none of the decisions.
    confirm_next: bool = False
    # The change the tests last passed on; a clean review of any other runs them.
    tests_digest: str = ''


def loop_memory(rounds: Iterable[ReviewRound]) -> LoopMemory:
    """The memory after ``rounds`` — every round BEFORE the one about to run."""
    memory = LoopMemory()
    for review_round in rounds:
        memory.confirm_next = False
        if review_round.tests is not None and review_round.tests.passed is not False:
            memory.tests_digest = review_round.diff_digest
        current = review_round.blocking_fingerprints
        if not current:
            # A clean review: a sweep was asked for (``clean``) or a changed
            # tree is reviewed again the same way (``changed``); failing tests
            # went back to be fixed (``tests_failed``). No fix claims carry over.
            if review_round.outcome == 'clean':
                memory.confirm_next = True
            elif review_round.outcome == 'changed':
                memory.confirm_next = review_round.sweep
            memory.previous = None
            continue
        if memory.previous is not None:
            memory.missed = count_missed_fixes(memory.missed, memory.previous, current)
        for finding in review_round.blocking:
            memory.first_ids.setdefault(finding.fingerprint, finding.id)
        memory.previous = frozenset(review_round.claimed_fixed_fingerprints)
    return memory

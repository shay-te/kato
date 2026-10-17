"""Has a step's change been merged? Decided from its change requests.

A step can touch several repositories, so each repository is judged on its own
and the step is merged only when every repository it changed is merged and
none is still open. Change requests created BEFORE the step's task existed
(or, for a task created at release, before the release) are ignored: a branch
name can repeat (a task id reused after a reset, the same plan split twice),
and an old merged request must never release a dependent. A request someone
opened after the task was created but before its turn still counts.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import Enum

from plan_queue_core_lib.plan_queue_core_lib.data.plan import RepoMerge, RepoMergeState
from plan_queue_core_lib.plan_queue_core_lib.ports import PullRequestFact

# Seconds to wait before looking again, by attempt; the last one repeats.
PROBE_BACKOFF_SECONDS = (15, 30, 60, 120, 300)
# Clocks differ between this machine and the git host; a request created a
# little "before" the release is still this step's.
CLOCK_SKEW_SECONDS = 120


class MergeVerdict(str, Enum):
    MERGED = 'merged'        # every repository merged (or untouched), at least one merged
    WAITING = 'waiting'      # something is open, unknown, or nothing was found yet
    DECLINED = 'declined'    # a repository's change was closed without merging


def classify_repository(
    repository_id: str, facts: Sequence[PullRequestFact], *, since: float,
) -> RepoMerge:
    """One repository's state from its change requests made after ``since``."""
    relevant = [fact for fact in facts if _created_since(fact, since)]
    url = next((fact.url for fact in relevant if fact.url), '')
    states = {fact.state for fact in relevant}
    if 'open' in states:
        return RepoMerge(repository_id, RepoMergeState.OPEN, _url(relevant, 'open') or url)
    if 'merged' in states:
        return RepoMerge(repository_id, RepoMergeState.MERGED, _url(relevant, 'merged') or url)
    if 'declined' in states:
        return RepoMerge(repository_id, RepoMergeState.DECLINED, url)
    return RepoMerge(repository_id, RepoMergeState.NONE)


def merge_verdict(repos: Sequence[RepoMerge]) -> MergeVerdict:
    states = [repo.state for repo in repos]
    if not states or RepoMergeState.UNKNOWN in states or RepoMergeState.OPEN in states:
        return MergeVerdict.WAITING
    if RepoMergeState.DECLINED in states:
        return MergeVerdict.DECLINED
    if RepoMergeState.MERGED in states:
        return MergeVerdict.MERGED
    return MergeVerdict.WAITING  # nothing found anywhere yet


def next_probe_delay(attempts: int) -> int:
    index = max(0, min(int(attempts), len(PROBE_BACKOFF_SECONDS) - 1))
    return PROBE_BACKOFF_SECONDS[index]


def found_nothing(repos: Sequence[RepoMerge]) -> bool:
    """Every repository answered and none had a change request."""
    return bool(repos) and all(repo.state == RepoMergeState.NONE for repo in repos)


def step_since(created_at: float, released_at: float) -> float:
    """From when a step's change requests count: its task's creation when the
    host knows it (a tracker sub-task), else its release."""
    return created_at or released_at


def _created_since(fact: PullRequestFact, since: float) -> bool:
    if not since or not fact.created_at:
        return True  # nothing to compare: keep it rather than lose a real merge
    created = _epoch(fact.created_at)
    if created is None:
        return True
    return created >= since - CLOCK_SKEW_SECONDS


def _epoch(text: str) -> float | None:
    try:
        return datetime.fromisoformat(text.strip().replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def _url(facts: Sequence[PullRequestFact], state: str) -> str:
    return next((fact.url for fact in facts if fact.state == state and fact.url), '')

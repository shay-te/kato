"""Builders and parsers for kato's namespaced task tags.

Every kato-recognized task tag lives under the ``kato:`` namespace
(``KATO_TAG_NAMESPACE``). Build and inspect tags through these helpers instead
of hand-writing ``'kato:...'`` strings, so the namespace and the segment names
(``repo``, ``triage``, ...) have exactly one definition. The raw constants live
in :mod:`kato_core_lib.data_layers.data.fields` (``RepositoryFields`` /
``TaskTags``); this module composes them.

There is a mirror of the repository-tag helpers on the web client at
``webserver/ui/src/utils/katoTags.js`` — keep the two in sync.
"""
from __future__ import annotations

import re

from kato_core_lib.data_layers.data.fields import (
    RepositoryFields,
    TaskTags,
)
from review_loop_core_lib.review_loop_core_lib.runner import (
    DEFAULT_MAX_ROUNDS,
    MAX_ROUNDS_LIMIT,
)
from utils_core_lib.utils_core_lib.text_utils import normalized_text


def build_repository_tag(repo_id: object) -> str:
    """Return the ``kato:repo:<repo_id>`` tag for a repository folder name."""
    return f'{RepositoryFields.REPOSITORY_TAG_PREFIX}{normalized_text(repo_id)}'


def repository_id_from_tag(tag: object) -> str:
    """Return the repo id from a ``kato:repo:<id>`` tag, preserving its case.

    Returns ``''`` for anything that isn't a repository tag or whose value
    after the prefix is blank — callers filter on the truthy result.
    """
    text = normalized_text(tag)
    if not text.lower().startswith(RepositoryFields.REPOSITORY_TAG_PREFIX):
        return ''
    return normalized_text(text[len(RepositoryFields.REPOSITORY_TAG_PREFIX):])


def build_triage_tag(outcome: object) -> str:
    """Return the ``kato:triage:<outcome>`` tag (e.g. ``'high'`` -> ``'kato:triage:high'``)."""
    return f'{TaskTags.TRIAGE_PREFIX}{normalized_text(outcome)}'


def build_branch_tag(branch: object) -> str:
    """Return the ``kato:branch:<branch>`` tag that records a task's named branch."""
    return f'{TaskTags.BRANCH_PREFIX}{normalized_text(branch)}'


def branch_from_tag(tag: object) -> str:
    """The branch a ``kato:branch:`` tag names, case kept; ``''`` for any other tag."""
    text = normalized_text(tag)
    if not text.lower().startswith(TaskTags.BRANCH_PREFIX):
        return ''
    return normalized_text(text[len(TaskTags.BRANCH_PREFIX):])


def task_branch(task_id: object, kato_tags: object) -> str:
    """The branch a task works on: the one kato's own tags name, else the task id.

    ``kato_tags`` are the tags kato recorded on the task (the workspace's
    ``tags``), never the tracker's. This is the ONE answer: code that has only
    a task id must ask here, not use the id as the branch.
    """
    for tag in kato_tags or ():
        branch = branch_from_tag(tag)
        if branch:
            return branch
    return normalized_text(task_id)


# The model families a ``kato:model:`` tag may name. Each is also a CLI alias
# that always resolves to that family's LATEST model, which is what a tag with
# no version asks for.
MODEL_FAMILIES = ('fable', 'opus', 'sonnet', 'haiku')

# ``5`` or ``5.5``: a major, and an optional minor (the CLI's own ids are
# ``claude-sonnet-5-5`` / ``claude-opus-5``). ``-`` is accepted for ``.``.
_MODEL_VERSION_RE = re.compile(r'^(\d+)(?:[.-](\d{1,2}))?$')


def build_model_tag(family: object, version: object = '') -> str:
    """Return ``kato:model:<family>`` or ``kato:model:<family>:<version>``."""
    tag = f'{TaskTags.MODEL_PREFIX}{normalized_text(family)}'
    version_text = normalized_text(version)
    return f'{tag}:{version_text}' if version_text else tag


def is_model_tag(tag: object) -> bool:
    """Whether ``tag`` is in the ``kato:model:`` namespace, valid or not."""
    return normalized_text(tag).lower().startswith(TaskTags.MODEL_PREFIX)


def model_from_tag(tag: object) -> str:
    """The model a ``kato:model:`` tag selects, or ``''`` when it selects none.

    * ``kato:model:sonnet``     -> ``sonnet`` — the alias, so the CLI resolves
      the LATEST sonnet (a version is optional on purpose).
    * ``kato:model:sonnet:5.5`` -> ``claude-sonnet-5-5`` — that release, pinned.
    * ``kato:model:opus:5``     -> ``claude-opus-5``; a ``.0`` minor is dropped,
      as the CLI's own ids do.

    ``''`` for a tag that is not a model tag AND for a malformed one (unknown
    family, extra segment, a version that is not a number); tell those apart
    with :func:`is_model_tag` so a typo can be reported rather than ignored.
    """
    text = normalized_text(tag).lower()
    if not text.startswith(TaskTags.MODEL_PREFIX):
        return ''
    parts = text[len(TaskTags.MODEL_PREFIX):].split(':')
    family = parts[0]
    if family not in MODEL_FAMILIES or len(parts) > 2:
        return ''
    if len(parts) == 1:
        return family
    match = _MODEL_VERSION_RE.match(parts[1])
    if match is None:
        return ''
    major, minor = match.group(1), int(match.group(2) or 0)
    return f'claude-{family}-{int(major)}' + (f'-{minor}' if minor else '')


_PINNED_MODEL_RE = re.compile(r'^claude-([a-z]+)-(\d{1,3})(?:-(\d{1,2}))?$')


def model_tag(model: object) -> str:
    """The ``kato:model:`` tag that selects ``model`` — :func:`model_from_tag`
    backwards — or ``''`` when no tag can say it (another provider's model, a
    context-size suffix). ``sonnet`` -> ``kato:model:sonnet``;
    ``claude-sonnet-5-5`` -> ``kato:model:sonnet:5.5``."""
    text = normalized_text(model).lower()
    if text in MODEL_FAMILIES:
        return build_model_tag(text)
    match = _PINNED_MODEL_RE.match(text)
    if match is None or match.group(1) not in MODEL_FAMILIES:
        return ''
    major, minor = match.group(2), match.group(3)
    return build_model_tag(match.group(1), f'{int(major)}.{int(minor)}' if minor else str(int(major)))


# Written as digits on purpose: ``\d`` also takes other scripts' digits, and the
# length keeps ``int`` away from a tag of thousands of them.
_REVIEW_ROUNDS_RE = re.compile(r'[0-9]{1,3}')


def build_review_loop_tag(rounds: object = '') -> str:
    """Return ``kato:review-loop`` or ``kato:review-loop:<rounds>``."""
    rounds_text = normalized_text(rounds)
    return f'{TaskTags.REVIEW_LOOP}:{rounds_text}' if rounds_text else TaskTags.REVIEW_LOOP


def is_review_loop_tag(tag: object) -> bool:
    """Whether ``tag`` is a ``kato:review-loop`` tag, valid or not."""
    text = normalized_text(tag).lower()
    return text == TaskTags.REVIEW_LOOP or text.startswith(f'{TaskTags.REVIEW_LOOP}:')


def review_rounds_from_tag(tag: object) -> int:
    """The rounds a ``kato:review-loop`` tag asks for, or ``0`` when it asks for none.

    * ``kato:review-loop``    -> the loop's default rounds.
    * ``kato:review-loop:10`` -> 10 rounds.

    ``0`` for a tag that is not a review-loop tag AND for a malformed one (not a
    whole number, or outside 1 to ``MAX_ROUNDS_LIMIT``); tell those apart with
    :func:`is_review_loop_tag` so a typo can be reported rather than ignored.
    """
    text = normalized_text(tag).lower()
    if text == TaskTags.REVIEW_LOOP:
        return DEFAULT_MAX_ROUNDS
    if not is_review_loop_tag(text):
        return 0
    value = text[len(TaskTags.REVIEW_LOOP) + 1:]
    if _REVIEW_ROUNDS_RE.fullmatch(value) is None:
        return 0
    rounds = int(value)
    return rounds if 1 <= rounds <= MAX_ROUNDS_LIMIT else 0

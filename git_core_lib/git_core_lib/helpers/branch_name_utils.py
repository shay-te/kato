"""Can this text be a git branch name?

Text checks only, with no git call, so a name a person typed can be refused
before there is a clone to run git in. Stricter than ``git check-ref-format
--branch`` on purpose: only characters every git host and file system takes, and
never a leading ``-``, which a git command line would read as an option.
"""

from __future__ import annotations

import re

MAX_BRANCH_NAME_CHARS = 200

_ALLOWED_CHARACTERS = re.compile(r'[A-Za-z0-9._/-]+')
_RESERVED_NAMES = frozenset({'head'})
# ``origin/feature`` pasted from ``git branch -a`` names the remote's copy, and
# a local branch called that makes every later ``origin/...`` ambiguous.
_RESERVED_FIRST_PARTS = frozenset({'origin', 'refs'})


def branch_name_problem(name: str) -> str:
    """Why ``name`` cannot be a branch, in words for a person; '' when it can."""
    if not name:
        return 'the name is empty'
    if len(name) > MAX_BRANCH_NAME_CHARS:
        return f'it is longer than {MAX_BRANCH_NAME_CHARS} characters'
    if _ALLOWED_CHARACTERS.fullmatch(name) is None:
        return 'use only letters, digits and . _ / -'
    if not name[0].isalnum():
        return 'it must start with a letter or a digit'
    if name.lower() in _RESERVED_NAMES:
        return f'{name} is a name git keeps for itself'
    parts = name.split('/')
    if parts[0].lower() in _RESERVED_FIRST_PARTS:
        return f'leave out {parts[0]}/ and give only the branch name'
    if '..' in name:
        return 'it cannot contain ..'
    if any(not part for part in parts):
        return 'it cannot have an empty part between slashes, or end with one'
    if any(part.startswith('.') for part in parts):
        return 'no part of it can start with a dot'
    if any(part.endswith('.lock') for part in parts):
        return 'no part of it can end with .lock'
    if name.endswith('.'):
        return 'it cannot end with a dot'
    return ''

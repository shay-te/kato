"""The tool split that makes an agent turn strictly read-only.

Two callers need the exact same guarantee — "this turn may look at the
workspace but must not change it" — and each used to spell the tool names out
inline. A read-only mode whose denylist drifts by one tool name is not
read-only, so the pair lives here once:

* ``READ_ONLY_DISALLOWED_TOOLS`` — every mutating tool, denied outright.
* ``READ_ONLY_ALLOWED_TOOLS`` — the inspection tools that stay available.

Both are CSV strings because that is the shape the CLI's ``--allowed-tools`` /
``--disallowed-tools`` flags take. Deny is the load-bearing half: allow-listing
alone would still let a tool the list forgot slip through.

Generic on purpose — no product, ticket, or workflow vocabulary — so any
transport can enforce a read-only turn the same way.
"""

from __future__ import annotations

# Mutating tools. ``WebFetch`` is here with the filesystem writers because a
# read-only turn should not reach the network either — it is an exfiltration
# path, not just a mutation one.
READ_ONLY_DISALLOWED_TOOLS = 'Edit,Write,MultiEdit,NotebookEdit,Bash,WebFetch'

# Inspection tools a read-only turn still needs to answer anything useful.
READ_ONLY_ALLOWED_TOOLS = 'Read,Glob,Grep'


def is_read_only_tool_set(disallowed: object) -> bool:
    """Whether ``disallowed`` denies everything ``READ_ONLY_DISALLOWED_TOOLS`` does.

    Compares as a SET, so ordering and any extra operator-configured denials
    don't produce a false negative. Used to recognize an already-spawned
    read-only session without threading the mode through separately.
    """
    names = {
        part.strip().lower()
        for part in str(disallowed or '').split(',')
        if part.strip()
    }
    required = {
        part.strip().lower()
        for part in READ_ONLY_DISALLOWED_TOOLS.split(',')
    }
    return required.issubset(names)

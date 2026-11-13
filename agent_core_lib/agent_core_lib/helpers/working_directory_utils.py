"""Where a CLI with no ``--add-dir`` should start, for a task with several repos.

Some agent CLIs (Grok, OpenCode) take ONE project directory: their sandbox or
their permission boundary is that directory, and they have no flag to add
another. A task whose repositories are siblings (``<task>/<repo-a>``,
``<task>/<repo-b>``) must then start in the folder that holds them all, or
every repository but the first is outside the agent's reach.
"""

from __future__ import annotations

import os


def single_project_root(cwd: str, additional_dirs=()) -> str:
    """The one directory a run should start in to reach ``cwd`` and every extra.

    ``cwd`` alone when there are no extras. Otherwise the folder holding them
    all — unless that is the filesystem root (no single project then; the
    first repository is the honest answer, and the extras stay out of reach
    rather than the whole disk coming into it).
    """
    directories = [str(path) for path in (cwd, *(additional_dirs or ())) if path]
    if len(directories) < 2:
        return str(cwd or '')
    try:
        common = os.path.commonpath(directories)
    except ValueError:
        return str(cwd)
    if os.path.dirname(common) == common:
        return str(cwd)
    return common

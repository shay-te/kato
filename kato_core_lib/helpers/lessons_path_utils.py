"""Single resolver for the lessons file path, shared by the writer and reader.

The lessons subsystem has two sides that MUST agree on one file:

  * the **writer** — ``LessonsService`` (via ``LessonsDataAccess``) writes the
    compacted global lessons to ``<state_dir>/lessons.md``.
  * the **reader** — the agent client reads ``claude.lessons_path`` on every
    spawn and injects that file into the system prompt.

These used to default differently: the writer fell back to the default lessons
file when ``KATO_LESSONS_PATH`` was unset, while the reader (the agent-client
factory) got the raw config value — an empty string — and read nothing. Net
effect: kato captured lessons but the agent never saw them, so it "learned
nothing" and repeated the same mistakes.

``resolve_and_sync_lessons_path`` resolves the path ONE way and writes the
resolved value back into the config, so the factory-driven reader and the
service-driven writer can never diverge.
"""

from __future__ import annotations

from pathlib import Path

from kato_core_lib.helpers.kato_paths_utils import kato_home_path

LESSONS_FILENAME = 'lessons.md'


def default_lessons_path() -> Path:
    """Default lessons file under ``KATO_WORKSPACES_ROOT``."""
    return (
        kato_home_path('workspaces', env_key='KATO_WORKSPACES_ROOT')
        / LESSONS_FILENAME
    )


DEFAULT_LESSONS_PATH = default_lessons_path()


def resolve_lessons_path(claude_cfg: object) -> Path:
    """Return the resolved lessons file path.

    An explicit ``claude.lessons_path`` (sourced from ``KATO_LESSONS_PATH``)
    wins, with ``~`` expanded; otherwise ``KATO_WORKSPACES_ROOT/lessons.md``.
    Pure — no side effects.
    """
    configured = ''
    if claude_cfg is not None:
        configured = str(getattr(claude_cfg, 'lessons_path', '') or '').strip()
    if configured:
        return Path(configured).expanduser()
    return default_lessons_path()


def resolve_and_sync_lessons_path(claude_cfg: object) -> Path:
    """Resolve the lessons path AND write it back into ``claude_cfg``.

    The write-back is the fix for the writer/reader divergence: after this call
    ``claude_cfg.lessons_path`` holds the same absolute path the writer uses, so
    the agent client (which reads that config value via the factory) injects the
    exact file ``LessonsService`` writes. Returns the resolved ``Path``.
    """
    path = resolve_lessons_path(claude_cfg)
    if claude_cfg is not None:
        try:
            claude_cfg.lessons_path = str(path)
        except Exception:
            # A read-only / struct-locked config can't be synced; the writer
            # still uses the resolved path, and the reader keeps whatever it had.
            pass
    return path

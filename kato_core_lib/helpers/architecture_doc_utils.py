"""Build a short pointer-directive for Claude's architecture doc.

When ``KATO_ARCHITECTURE_DOC_PATH`` is set, kato appends a short
directive to Claude's system prompt on every spawn instructing it
to ``Read`` the file at the start of every task. We do **not**
inline the file body — even moderate architecture docs (50KB+)
push the spawn argv past Windows' CreateProcess limit (~32K
chars) and trip ``[WinError 206] The filename or extension is too
long``. Claude's ``Read`` tool fetches the content on demand; the
trade-off is one extra read per session, which is cheap.

The directive is recomputed on every spawn so editing the doc's
*location* (env var change) takes effect on the next turn without
restarting kato. Read errors at the path-existence check are
logged and treated as "no doc configured" — the orchestrator
never blocks on this.
"""

from __future__ import annotations

import logging
from pathlib import Path

from kato_core_lib.helpers.text_utils import normalized_text


_LIVING_DOC_DIRECTIVE_TEMPLATE = (
    'Project architecture document: {path}\n'
    'At the start of every task, use the Read tool to read this '
    'file. It contains the canonical map of the workspace and any '
    'non-obvious conventions, hidden contracts, gotchas, and layer '
    'boundaries the project has accumulated. Let it shape your '
    'plan.\n'
    '\n'
    'Treat it as a living document you are responsible for keeping '
    'accurate. While working, if you discover something not yet '
    'documented that would help a future agent (a non-obvious '
    'convention, a hidden contract, a gotcha, a layer boundary, a '
    '"why we do it this way"), update the file via the Edit tool — '
    'append a new sub-section under the most appropriate top-level '
    'section, or add a new section if none fits. Do not duplicate '
    'content already documented; do not restate what the code shows. '
    'The document is a navigation aid and a contract registry, not '
    'a mirror of the source. Kato commits and pushes the file (you '
    'must NEVER run git); just edit.\n'
)


def read_architecture_doc(
    path: str,
    *,
    logger: logging.Logger | None = None,
) -> str:
    """Return the pointer directive, or '' when nothing is configured.

    Returns a short fixed-size directive (~700 chars) telling Claude
    to ``Read`` the architecture doc at the configured path. We do
    **not** inline the file body — that historically pushed the
    spawn argv past Windows' CreateProcess limit (~32K chars) for
    any non-trivial doc.

    ``path`` may be an empty string (returns ''), a missing file
    (warns once + returns ''), or a real file (returns the
    directive). The file content itself is never read here — only
    its existence is checked, so kato fails fast if the operator
    configured a stale path.
    """
    normalized = normalized_text(path)
    if not normalized:
        return ''
    file_path = Path(normalized).expanduser()
    if not file_path.is_file():
        if logger is not None:
            logger.warning(
                'architecture doc path %s is not a file; skipping context injection',
                file_path,
            )
        return ''
    return _LIVING_DOC_DIRECTIVE_TEMPLATE.format(path=str(file_path))

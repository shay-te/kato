"""UTF-8-safe defaults for ``subprocess.run`` / ``subprocess.Popen``.

Why this module exists: on Windows, Python's ``subprocess`` defaults
to ``locale.getpreferredencoding(False)`` for text-mode reads, which
is ``cp1252`` on most Western installs. When a child process emits
UTF-8 output containing a byte not in cp1252 (``0x9d`` and friends),
the stdout reader thread crashes with::

    UnicodeDecodeError: 'charmap' codec can't decode byte 0x9d ...

Kato spawns plenty of these children — ``git diff`` over commit
messages with smart quotes, ``claude --print`` returning Markdown
with em-dashes, ``npm audit`` JSON with non-ASCII strings, ``bandit``
reports — and the failure mode is silent in the orchestrator until
something tries to consume the swallowed output.

The fix is to pin ``encoding='utf-8'`` and ``errors='replace'`` on
every text-mode subprocess. ``replace`` over ``ignore`` so genuinely
malformed bytes leave a visible ``\\ufffd`` in the captured text
rather than silently dropping data.

Use the ``SAFE_TEXT_KWARGS`` dict via ``**SAFE_TEXT_KWARGS`` at every
text-mode call site.
"""

from __future__ import annotations


# Spread on every text-mode ``subprocess.run`` / ``Popen`` call.
# Using a dict instead of helper functions keeps the call sites
# readable: ``subprocess.run(cmd, capture_output=True, **SAFE_TEXT_KWARGS)``
# is exactly the shape the rest of the codebase expects.
SAFE_TEXT_KWARGS: dict = {
    'encoding': 'utf-8',
    'errors': 'replace',
}

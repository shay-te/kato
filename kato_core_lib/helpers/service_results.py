"""Building the result dicts the service layer hands back to the UI.

Every service operation answers a plain dict that a Flask route serialises
straight to JSON, and every failure answers the same shape: the operation's
own flag set ``False``, whatever context the caller needs to identify the
task, and an ``error`` string the UI shows verbatim.

Spelled by hand at forty-odd sites, that shape drifts — a route reading
``result['error']`` sees ``None`` when a producer writes ``errors``, and
nothing raises. Built here, it cannot.
"""

from __future__ import annotations


def failure(error: object, *, flag: str = 'ok', **context) -> dict[str, object]:
    """A failed operation's result: ``{flag: False, **context, 'error': ...}``.

    ``flag`` is the operation's own success key — ``pushed`` for a push,
    ``created`` for a pull request, ``ok`` for everything that has no better
    verb. ``context`` carries whatever the caller needs to identify what
    failed (usually ``task_id``).
    """
    result: dict[str, object] = {flag: False}
    result.update(context)
    result['error'] = str(error)
    return result

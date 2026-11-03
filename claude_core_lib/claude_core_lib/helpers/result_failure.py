"""Why a failed turn ended, in the CLI's own words.

A failed ``result`` event carries its reason in ``result`` or, when the turn
failed during execution, in ``errors`` with no ``result`` at all:

    {"type": "result", "subtype": "error_during_execution", "is_error": true,
     "errors": ["No conversation found with session ID: ..."]}
"""

from __future__ import annotations

from utils_core_lib.utils_core_lib.text_utils import condensed_text


def failure_reason(raw: dict) -> str:
    """The reason on a ``result`` event; ``''`` when it gives none."""
    result = condensed_text(raw.get('result', ''))
    if result:
        return result
    errors = raw.get('errors')
    if not isinstance(errors, list):
        return ''
    return '; '.join(filter(None, (condensed_text(error) for error in errors)))

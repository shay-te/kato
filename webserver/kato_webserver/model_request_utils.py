"""Read a model id out of a request body — one rule for every route.

An absent or empty model means "the default"; anything else must look like a
model id, so a request can never smuggle an arbitrary string onto a command
line as ``--model``.
"""

from __future__ import annotations

import re

# A model id as the CLIs take it: an alias (``opus``) or a pinned id
# (``claude-opus-5-5[1m]``). It goes into argv, never through a shell; the
# pattern only keeps junk from reaching the spawn as a confusing CLI error.
_MODEL_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,99}')


def requested_model(body: object) -> tuple[str, str]:
    """``(model, problem)``; an absent / empty model is the default ('')."""
    source = body if isinstance(body, dict) else {}
    model = source.get('model')
    if model is None or model == '':
        return '', ''
    if not isinstance(model, str) or not _MODEL_ID.fullmatch(model.strip()):
        return '', f'model must be a model id, got {model!r}'
    return model.strip(), ''

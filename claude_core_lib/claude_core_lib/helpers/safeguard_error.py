"""Recognise the API's "safeguards flagged this session" refusal.

When Anthropic's acceptable-use safeguards reject a request server-side, the
CLI ends the turn with an error whose text reads like:

    API Error: Opus 5.5's safeguards flagged this session
    (https://www.anthropic.com/legal/aup). ...
    Details: [cyber]
    Request ID: req_011...

It is a REFUSAL, not a transport failure: the same prompt fails the same way
every time on that model, and the host's own recovery (re-resume, respawn on
the SAME model) cannot clear it — only a different model, or a reworded
request, can. The message itself notes it "can sometimes flag non-cybersecurity
work", so the host surfaces it as a distinct, actionable state (offer a retry
on another model) rather than a generic "turn errored".

Pure and signature-based: it matches the stable parts of the message (the AUP
link + "flagged this session") so a change to the model NAME in the text does
not blind it, and pulls out the request id and the ``Details`` tag for the
operator. No model list, no host specifics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# The two stable anchors. The model name drifts ("Opus 5.5" → whatever is
# current) and the surrounding prose is reworded, but a safeguard refusal
# always links the AUP and says the session was flagged.
_AUP_LINK = re.compile(r'anthropic\.com/legal/aup', re.IGNORECASE)
_FLAGGED = re.compile(r'safeguards?\s+flagged\s+th(?:is|e)\s+session', re.IGNORECASE)

_REQUEST_ID = re.compile(r'Request ID:\s*(req_[A-Za-z0-9]+)', re.IGNORECASE)
_DETAILS = re.compile(r'Details:\s*\[([^\]]+)\]', re.IGNORECASE)
# "Opus 5.5's safeguards" — the model the refusal came from, when stated.
_MODEL = re.compile(r'([A-Z][A-Za-z0-9.\- ]*?)\'s safeguards', re.IGNORECASE)


@dataclass(frozen=True)
class SafeguardFlag(object):
    """A parsed safeguard refusal."""

    #: The ``Details: [...]`` tag, e.g. ``cyber`` ('' when the text omits it).
    details: str = ''
    #: The ``Request ID: req_...`` ('' when absent).
    request_id: str = ''
    #: The model named in the text, e.g. ``Opus 5.5`` ('' when absent).
    model: str = ''


def is_safeguard_flag(text: str) -> bool:
    """True when ``text`` is a safeguards refusal (both anchors present)."""
    value = str(text or '')
    return bool(_AUP_LINK.search(value) and _FLAGGED.search(value))


def classify_safeguard_error(text: str) -> SafeguardFlag | None:
    """A :class:`SafeguardFlag` when ``text`` is a safeguards refusal, else None.

    ``text`` is whatever the turn surfaced — the result message, the stderr
    tail, or the two joined; the anchors are matched anywhere in it.
    """
    value = str(text or '')
    if not is_safeguard_flag(value):
        return None
    request_id = _REQUEST_ID.search(value)
    details = _DETAILS.search(value)
    model = _MODEL.search(value)
    return SafeguardFlag(
        details=details.group(1).strip() if details else '',
        request_id=request_id.group(1) if request_id else '',
        model=model.group(1).strip() if model else '',
    )

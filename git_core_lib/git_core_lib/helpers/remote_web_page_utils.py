"""A web page a git remote printed, read as one line of its visible text.

When a hosted git server fails inside its own server-side check, it can send
its HTML error page down the push, and git prints every line of it after
``remote:`` — a doctype, a stylesheet, an SVG logo, 120 lines of markup. A
failure message keeps the LAST lines, so all that was left was the bottom of
the page (``</div> </body> </html>``) above "pre-receive hook declined", which
reads like a rule refusing the push. What the page SAYS ("Something went
wrong… check our status page") is the message: kept, with its links.
"""

from __future__ import annotations

import html
import re

REMOTE_PREFIX = 'remote:'
PAGE_LINE_PREFIX = f'{REMOTE_PREFIX} the server sent an error page:'

_PAGE_START = re.compile(r'<!doctype\s+html|<html[\s>]', re.IGNORECASE)
_PAGE_END = re.compile(r'</html\s*>', re.IGNORECASE)
# Nothing a reader sees: the head (title, styles), scripts, drawings.
_INVISIBLE = re.compile(r'<(head|style|script|svg|noscript)\b[\s\S]*?</\1\s*>', re.IGNORECASE)
_LINK = re.compile(r'<a\b[^>]*?\bhref\s*=\s*"([^"]+)"[^>]*>([\s\S]*?)</a\s*>', re.IGNORECASE)
_TAG = re.compile(r'<[^>]*>')
_SPACES = re.compile(r'\s+')


def collapse_remote_web_pages(lines: list[str]) -> list[str]:
    """``lines`` with each HTML page the remote printed as one line of its text.

    A page starts at a ``remote:`` line opening a document (a doctype or
    ``<html``) and ends at ``</html>`` — or where the remote's lines stop, if
    it never closes. Every other line is kept as it is.
    """
    kept: list[str] = []
    page: list[str] | None = None
    for line in lines:
        body = line[len(REMOTE_PREFIX):] if line.startswith(REMOTE_PREFIX) else None
        if page is not None and body is None:
            kept.append(_page_line(page))
            page = None
        if page is None:
            if body is None or not _PAGE_START.search(body):
                kept.append(line)
                continue
            page = []
        page.append(body)
        if _PAGE_END.search(body):
            kept.append(_page_line(page))
            page = None
    if page is not None:
        kept.append(_page_line(page))
    return kept


def _page_line(page: list[str]) -> str:
    markup = _INVISIBLE.sub(' ', '\n'.join(page))
    markup = _LINK.sub(lambda link: f'{link.group(2)} ({link.group(1)})', markup)
    text = _SPACES.sub(' ', html.unescape(_TAG.sub(' ', markup))).strip()
    return f'{PAGE_LINE_PREFIX} {text}' if text else PAGE_LINE_PREFIX

"""The release-notes markdown as HTML, for the Google Doc copy.

Only the shapes the release-notes document uses: ``#``/``##``/``###``
headings, ``-`` list items, ``[text](url)`` links, plain paragraphs and code
fences. Everything is escaped first, so the operator's own text can never
become markup; kato's invisible ``<!-- kato:task … -->`` markers are dropped.
"""

from __future__ import annotations

import html
import re

_MARKER = re.compile(r'\s*<!--\s*kato:task\s+\S+?\s*-->')
_HEADING = re.compile(r'^(#{1,6})\s+(.*)$')
_ITEM = re.compile(r'^\s*[-*]\s+(.*)$')
_FENCE = re.compile(r'^\s*(```|~~~)')
_LINK = re.compile(r'\[([^\]]+)\]\((https?://[^\s)]+)\)')


def release_notes_html(markdown: str, *, title: str = 'Release notes') -> str:
    body: list[str] = []
    in_list = False
    in_code = False
    for raw in (markdown or '').splitlines():
        if _FENCE.match(raw):
            if in_list:
                body.append('</ul>')
                in_list = False
            body.append('</pre>' if in_code else '<pre>')
            in_code = not in_code
            continue
        if in_code:
            body.append(html.escape(raw))
            continue
        line = _MARKER.sub('', raw).rstrip()
        item = _ITEM.match(line)
        if item:
            if not in_list:
                body.append('<ul>')
                in_list = True
            body.append(f'<li>{_inline(item.group(1))}</li>')
            continue
        if in_list:
            body.append('</ul>')
            in_list = False
        heading = _HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            body.append(f'<h{level}>{_inline(heading.group(2))}</h{level}>')
        elif line.strip():
            body.append(f'<p>{_inline(line.strip())}</p>')
    if in_list:
        body.append('</ul>')
    if in_code:
        body.append('</pre>')
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        f'<title>{html.escape(title)}</title></head><body>\n'
        + '\n'.join(body)
        + '\n</body></html>\n'
    )


def _inline(text: str) -> str:
    """Escape, then turn ``[text](https://…)`` back into links."""
    escaped = html.escape(text, quote=False)
    return _LINK.sub(
        lambda match: f'<a href="{html.escape(match.group(2), quote=True)}">{match.group(1)}</a>',
        escaped,
    )

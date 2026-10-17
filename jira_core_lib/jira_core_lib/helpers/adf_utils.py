"""Build Atlassian Document Format (ADF) bodies for Jira Cloud REST v3.

v3 takes every rich-text field — a comment body, an issue description — as an
ADF document, not a plain string: ``{"body": "text"}`` is rejected with a 400.
These builders turn plain text into the smallest valid document: a ``doc`` of
paragraphs, plus a ``codeBlock`` for text that must be shown exactly as written.

ADF forbids an empty ``text`` node, so a blank line becomes no node at all —
paragraphs already render apart, and the code block keeps its blank lines
inside its one text node.
"""
from __future__ import annotations

ADF_VERSION = 1


def adf_document(*blocks: dict) -> dict:
    """A top-level ``doc`` node holding ``blocks`` in order."""
    return {'type': 'doc', 'version': ADF_VERSION, 'content': list(blocks)}


def adf_paragraphs(text: object) -> list[dict]:
    """One ``paragraph`` per non-blank line of ``text``."""
    return [
        {'type': 'paragraph', 'content': [{'type': 'text', 'text': line}]}
        for line in str(text or '').splitlines()
        if line.strip()
    ]


def adf_code_block(text: object) -> dict:
    """A ``codeBlock`` whose single text node is ``text`` byte-for-byte."""
    text = str(text or '')
    return {
        'type': 'codeBlock',
        'content': [{'type': 'text', 'text': text}] if text else [],
    }


def adf_from_text(text: object) -> dict:
    """A plain-text body as an ADF document of paragraphs."""
    return adf_document(*adf_paragraphs(text))

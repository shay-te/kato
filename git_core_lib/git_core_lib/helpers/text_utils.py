"""Minimal text normalisation helpers for git_core_lib (no external deps)."""
from __future__ import annotations


def normalized_text(value: object) -> str:
    return str(value or '').strip()


def normalized_lower_text(value: object) -> str:
    return normalized_text(value).lower()


def text_from_attr(obj: object, key: str, default: object = '') -> str:
    return normalized_text(getattr(obj, key, default) or default)

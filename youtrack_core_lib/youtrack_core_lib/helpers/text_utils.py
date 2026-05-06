"""Text normalisation utilities — no external dependencies."""
from __future__ import annotations

from collections.abc import Mapping


def normalized_text(value: object) -> str:
    return str(value or '').strip()


def normalized_lower_text(value: object) -> str:
    return normalized_text(value).lower()


def condensed_lower_text(value: object) -> str:
    return ' '.join(normalized_text(value).split()).lower()


def alphanumeric_lower_text(value: object) -> str:
    return ''.join(ch for ch in normalized_lower_text(value) if ch.isalnum())


def text_from_mapping(
    mapping: Mapping[object, object] | None,
    key: object,
    default: object = '',
) -> str:
    if not isinstance(mapping, Mapping):
        return normalized_text(default)
    return normalized_text(mapping.get(key, default))

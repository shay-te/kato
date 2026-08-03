"""Text normalization and safe attribute/mapping reads.

The canonical copy. These helpers were independently defined in FIVE libraries
— ``agent_core_lib``, ``git_core_lib``, ``kato_core_lib``,
``provider_client_base``, ``youtrack_core_lib`` — because every lib that parses
provider payloads needs "give me a clean string from this possibly-missing,
possibly-``None`` field".

Five copies of a string trimmer looks harmless. It was not: the copies had
drifted into three genuinely different contracts, and each difference fails
QUIETLY, returning a plausible empty value instead of raising. Where each copy
landed, and how much it actually cost:

  * ``text_from_mapping`` required a real ``Mapping`` in three copies and
    duck-typed on ``.get`` in the others. The duck-typed form is kept here: it
    is a strict superset — identical for ``dict`` and for an ``omegaconf``
    ``DictConfig``, which registers as a ``Mapping`` — and additionally accepts
    the ``SimpleNamespace(get=...)`` stand-ins tests use.
  * ``text_from_attr`` had an ``or default`` in the ``git_core_lib`` copy, so a
    present-but-falsy attribute fell back to the default instead of ``''``.
    No caller passed a non-empty default, so the difference was unobservable —
    but only by luck, and the parameter was also named ``key`` there rather
    than ``attribute``, which would have broken any keyword call.
  * ``dict_from_mapping`` gated on ``isinstance(mapping, dict)`` in one copy and
    ``Mapping`` in another. ``Mapping`` is kept as the wider gate, but be
    accurate about what that buys: it is LATENT, not a live bug fix. It changes
    the answer only for a non-``dict`` ``Mapping`` holding real ``dict`` values
    (``MappingProxyType`` and the like), and every caller today walks
    parsed-JSON provider payloads, which are always real ``dict``/``list``. An
    ``omegaconf`` config does NOT round-trip through either version — the outer
    gate passes but the nested ``DictConfig`` value fails the inner
    ``isinstance(value, dict)`` check, so both copies return ``{}``.
"""

from __future__ import annotations

from collections.abc import Mapping


def normalized_text(value: object) -> str:
    """``value`` as a stripped string; ``None``/falsy becomes ``''``."""
    return str(value or '').strip()


def normalized_lower_text(value: object) -> str:
    """:func:`normalized_text`, lowercased — for case-insensitive comparison."""
    return normalized_text(value).lower()


def condensed_text(value: object) -> str:
    """:func:`normalized_text` with every run of whitespace collapsed to one space."""
    return ' '.join(normalized_text(value).split())


def condensed_lower_text(value: object) -> str:
    """:func:`condensed_text`, lowercased."""
    return condensed_text(value).lower()


def alphanumeric_lower_text(value: object) -> str:
    """Lowercased, with every non-alphanumeric character dropped.

    For comparing identifiers whose punctuation and spacing are cosmetic —
    provider field names, workflow status labels — so ``"In Review"``,
    ``"in-review"`` and ``"IN_REVIEW"`` all compare equal.
    """
    return ''.join(
        character for character in normalized_lower_text(value) if character.isalnum()
    )


def text_from_attr(obj: object, attribute: str, default: object = '') -> str:
    """Normalized string from ``obj.attribute``, or the default when absent.

    A present-but-falsy attribute yields ``''``, NOT the default — the default
    covers "the attribute isn't there", not "the attribute is empty".
    """
    return normalized_text(getattr(obj, attribute, default))


def text_from_mapping(mapping, key, default: object = '') -> str:
    """Read ``key`` from any ``.get(key, default)``-supporting object.

    Duck-typed on purpose — works for ``dict``, ``omegaconf`` configs,
    ``SimpleNamespace(get=...)`` stand-ins in tests, and anything else that
    quacks like a mapping. ``None`` (and any other object without a usable
    ``.get``) yields the normalized default.
    """
    if mapping is None:
        return normalized_text(default)
    getter = getattr(mapping, 'get', None)
    if not callable(getter):
        return normalized_text(default)
    return normalized_text(getter(key, default))


def dict_from_mapping(mapping: object, key: object) -> dict:
    """Read ``key`` from ``mapping`` when the value is a dict, else ``{}``.

    Saves every caller an ``isinstance`` dance when walking nested provider
    payloads, where an absent field can arrive as ``None``, a string, or a
    list rather than the expected object.

    The container is gated on ``Mapping`` (wider than ``dict``); the VALUE is
    gated on ``dict``, because a caller asking for a dict is about to index it.
    """
    if not isinstance(mapping, Mapping):
        return {}
    value = mapping.get(key)
    return value if isinstance(value, dict) else {}


def list_from_mapping(mapping: object, key: object) -> list:
    """Read ``key`` from ``mapping`` when the value is a list, else ``[]``.

    The list-shaped counterpart to :func:`dict_from_mapping`, with the same
    ``Mapping`` gate for the same reason.
    """
    if not isinstance(mapping, Mapping):
        return []
    value = mapping.get(key)
    return value if isinstance(value, list) else []

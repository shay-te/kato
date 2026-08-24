"""Explicit late binding for injected collaborators.

A sub-service is built once but its collaborators can be replaced afterwards
(setup mode rebuilds the managers; a test swaps one in). Capturing the object
at construction leaves the sub-service talking to a stale reference while
everything else moved on.

The fix is a marker, not a guess. ``later(host, 'attr')`` says "resolve this
per call"; anything else passed in is the collaborator itself::

    service = TaskPublishService(
        repository_service=later(self, '_repository_service'),  # per call
        task_service=task_service,                              # this object
    )

The obvious shortcut — "if it's callable it must be a getter" — is a trap: a
``Mock`` is callable, so every test that injected a mock silently got
``mock()`` (a child mock) instead of the mock it passed. Nothing raised; the
assertions just never saw a call. Hence the marker.
"""

from __future__ import annotations

from typing import Any, Callable


class _Later(object):
    """A collaborator to fetch from ``host.attribute`` on every access."""

    __slots__ = ('host', 'attribute')

    def __init__(self, host: Any, attribute: str) -> None:
        self.host = host
        self.attribute = attribute

    def __call__(self) -> Any:
        return getattr(self.host, self.attribute)


def later(host: Any, attribute: str) -> _Later:
    """Mark a constructor argument as read from ``host.attribute`` per call."""
    return _Later(host, attribute)


def provider_for(value: Any) -> Callable[[], Any]:
    """Return a zero-arg getter for a constructor argument.

    ``later(...)`` values resolve on every call; everything else — including
    callables such as mocks and factories — is returned as-is, unwrapped.
    """
    if isinstance(value, _Later):
        return value
    return lambda bound=value: bound


def call_later(host: Any, attribute: str) -> Callable[..., Any]:
    """A function that looks ``host.attribute`` up at CALL time and calls it.

    For host *methods* handed to a sub-service as callbacks: binding
    ``host.method`` directly freezes whatever was defined at construction, so
    a later override (or a test's ``patch.object``) is never seen.
    """
    def _call(*args: Any, **kwargs: Any) -> Any:
        return getattr(host, attribute)(*args, **kwargs)

    _call.__name__ = attribute.lstrip('_')
    return _call

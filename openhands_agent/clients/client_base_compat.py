from __future__ import annotations

try:
    from core_lib.client.client_base import ClientBase
except ModuleNotFoundError:
    class ClientBase:  # pragma: no cover - compatibility shim for local scaffolding
        def __init__(self, *args, **kwargs) -> None:
            pass

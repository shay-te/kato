"""Light, import-cheap error types shared between ``main`` and the core lib.

``main`` keeps its imports lazy (proxies) so boot stays light; exception
types it must catch by identity live here instead of in the heavyweight
``kato_core_lib`` module.
"""

from __future__ import annotations


class AgentBackendChangedError(RuntimeError):
    """The agent backend picked during SETUP MODE differs from the one the
    setup boot built its managers for. The managers are backend-shaped and
    the webserver holds references to them, so the switch cannot be applied
    live — ``main``'s setup wait loop reacts by re-exec'ing the kato process
    in place (terminal-free restart)."""

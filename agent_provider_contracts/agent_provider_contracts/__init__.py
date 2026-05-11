"""Shared provider contracts for agent backend integrations.

Defines the ``AgentProvider`` Protocol every backend (claude_core_lib,
openhands_core_lib, future codex_core_lib, …) implements, plus the
DTOs that flow across the boundary. Pure ABCs + DTOs — zero runtime
dependencies, zero implementation.
"""

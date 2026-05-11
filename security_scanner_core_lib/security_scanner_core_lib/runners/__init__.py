"""Per-tool runners for the security scanner.

Each runner is a function ``run(workspace_path, logger=None,
timeout_seconds=None) -> list[SecurityFinding]`` that knows nothing
about kato services or the orchestrator. The orchestrator wires
them, applies timeouts, dedupes results, and decides block/warn.

Adding a new runner:

1. Drop a module here exposing the ``run`` signature above.
2. Register it in ``security_scanner_service.default_config()``
   (or the operator's runner-config block).

Each runner must:

- Catch its own tool's exceptions and either return ``[]`` or raise
  ``RunnerUnavailableError`` (the orchestrator surfaces those as
  warnings, not blockers — missing tool ≠ security issue).
- Resolve all paths relative to ``workspace_path`` and never
  traverse outside it.
- Skip ``.git/``, ``node_modules/``, ``.venv/``, and the standard
  ``EXCLUDE_DIRS`` set defined in ``_helpers.py``.

Imports are explicit at call sites — repo convention forbids
``__all__`` shims here.
"""

# OpenHands Rules

This repository uses OpenHands to implement YouTrack tasks and fix review comments.

## Architecture

- Keep orchestration logic in services.
- Keep external API calls inside clients and data-access layers.
- Do not add pass-through helper methods on `OpenHandsAgentCoreLib` when the service can be used directly.
- Prefer constants from `fields.py` over free-text field names.
- Reuse existing utilities before introducing duplicate helper logic.

## Required Behavior

- Read the full task context before changing code.
- Prefer small, direct changes over broad refactors.
- Keep the existing project structure and naming patterns.
- Do not add dependencies unless they are clearly required.
- Make configuration-driven behavior live in config or environment variables, not hardcoded values.

## Testing

- Write tests for new behavior when possible.
- Prefer focused unit tests close to the changed behavior.
- Run the relevant tests before opening a pull request.
- If tests fail, fix the code or the tests and rerun them until they pass.
- Add edge-case coverage for malformed payloads, retries, timeouts, and degraded downstream behavior when relevant.
- In tests, prefer existing entities and shared test helpers over ad hoc objects.
- Do not add test bootstrap shims or fake package injectors to work around missing required dependencies.
- Tests in this repository must run against the real installed packages and should fail fast if those packages are missing.
- Never reintroduce `tests/bootstrap.py`-style import patching, `sys.modules` injection, or fake package facades for required dependencies.
- If a test only passes with a shim, fix the environment or update the test to use the real package API instead of recreating the package in tests.
- Do not mock third-party libraries or `core-lib` behavior when the real installed package can be used directly in the test.
- Do not add mocks just to force a preferred exception type or API shape if the real library behavior is acceptable.
- Prefer lightweight real objects and real library calls over `Mock()` or `SimpleNamespace()` when the dependency is already cheap and deterministic.

## Safety

- Do not remove user code unless it is part of the requested fix.
- Do not use destructive git commands.
- Preserve configuration compatibility unless the task explicitly changes it.
- Keep logging clear on any swallowed exception or degraded path.
- Do not add fallback code that hides missing required packages; required runtime dependencies should fail fast instead of being silently skipped or patched around.
- Do not add fallback config-shape handling for required settings; access the expected config directly and let invalid config fail fast.
- Do not add thin wrappers around upstream library methods unless they provide real domain behavior for this app.
- Do not translate upstream exceptions into local ones unless the boundary needs a deliberate application-level contract.

## Output

- Keep code readable.
- Reuse existing utilities instead of duplicating logic.
- Log failures clearly when a flow continues after an error.
- Before opening a pull request, make sure the implementation prompt instructions were followed, especially around tests.

## Code Style

### Philosophy

This codebase favors:

- clarity over cleverness
- explicit structure over magic
- minimal output over verbosity
- small reusable helpers over duplication

The goal is to keep the code:

- easy to read
- easy to modify
- predictable during execution

### Avoid Duplication

- If logic or values appear more than once, extract them.
- Extract repeated paths, hosts, filenames, and project names into constants.
- Extract repeated workflow steps such as git, scp, and path building into helpers.
- Prefer small reusable helpers over copy-pasted branches of similar code.

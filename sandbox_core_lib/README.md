# sandbox-core-lib

Hardened Docker sandbox for LLM-CLI agents. Originally lived
inside kato as `kato_core_lib.sandbox`; extracted because
sandboxing is a security product boundary, not an internal helper,
and benefits from being audited as a standalone unit.

## What it does

Wraps every agent spawn (today: the Claude CLI) in a hardened
Docker container so an autonomous agent cannot reach beyond the
per-task workspace it was given. The library owns the *mechanics*
of containment; consumers (kato, future agent-orchestration tools)
own the *policy* of when to apply it.

Concretely the library provides:

- **`manager.py`** — image build/verify, command wrapping, workspace
  mount validation, container spawn, audit logging with hash
  chaining, spawn rate limiting, credential leak detection on
  container output.
- **`tls_pin.py`** — TOFU certificate pinning for `api.anthropic.com`
  so a rogue CA cannot impersonate the model endpoint.
- **`audit_log_shipping.py`** — externalises the local audit log to
  S3-with-Object-Lock or a syslog forwarder so tail-truncation is
  detectable.
- **`credential_patterns.py`** — pattern bank for detecting leaked
  secrets and phishing strings in container output.
- **`workspace_delimiter.py`** — `<UNTRUSTED_WORKSPACE_FILE>…</…>`
  framing for prompt-injection hardening on workspace content.
- **`system_prompt.py`** — sandbox-aware system-prompt addendum
  (data-vs-instructions guidance, untrusted-content delimiter
  protocol).
- **`bypass_permissions_validator.py`** — the startup gate that
  refuses unsafe combinations of `KATO_CLAUDE_DOCKER` /
  `KATO_CLAUDE_BYPASS_PERMISSIONS` (e.g. bypass without docker, or
  any sandbox flag while running as root).
- **`verify.py`** — end-to-end smoke test of the sandbox image.
- **`Dockerfile`** + `entrypoint.sh` + `init-firewall.sh` — the
  hardened image itself, with egress-firewall init.

## Public API

Each module exposes a small set of named entry points called from
`kato_core_lib/main.py` (startup preflight) and from the Claude
CLI clients (per-spawn wrap). Notable ones:

```python
from sandbox_core_lib.sandbox_core_lib.manager import (
    ensure_image, wrap_command, check_spawn_rate, record_spawn,
    enforce_no_workspace_secrets, make_container_name,
)
from sandbox_core_lib.sandbox_core_lib.tls_pin import (
    enforce_anthropic_tls_pin,
)
from sandbox_core_lib.sandbox_core_lib.bypass_permissions_validator import (
    is_docker_mode_enabled, is_read_only_tools_enabled,
    validate_bypass_permissions_safety,
)
from sandbox_core_lib.sandbox_core_lib.system_prompt import (
    compose_system_prompt,
)
from sandbox_core_lib.sandbox_core_lib.workspace_delimiter import (
    wrap_untrusted_workspace_content,
)
```

Calling these is the consumer's job; the library only enforces
what it owns (image build, mount validation, command shape,
audit chain integrity, TLS pin lifecycle).

## Documentation

See **[`SANDBOX_PROTECTIONS.md`](SANDBOX_PROTECTIONS.md)** for the
full attack-map, residuals, and "open gap" closure tracking. That
doc is pinned by drift-guard tests in
[`sandbox_core_lib/tests/`](sandbox_core_lib/tests/) — every claim
the doc makes must match a constant or behaviour in the code.

## Tests

Inside the package:

```
sandbox_core_lib/sandbox_core_lib/tests/
```

Run via `./kato test` (kato's test runner discovers both kato's
own tests and this package's tests in one invocation).

## Why a separate package

- Auditable as one artefact — security reviewers don't have to dig
  through an orchestrator codebase to find the containment layer.
- Reusable — any other agent-runner (not just kato) can adopt it.
- Drift-resistant — protections doc lives next to the code it
  describes; tests pin both together.

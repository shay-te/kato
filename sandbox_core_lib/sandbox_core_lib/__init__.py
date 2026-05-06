"""Hardened Docker sandbox for LLM-CLI agents.

Extracted from the application layer as an independent security boundary.
The reusable mechanics (image build/verify, command wrapping, workspace mount
validation, audit log shipping with hash chaining, TLS pinning, credential
pattern detection, untrusted-content delimiter framing) all live here.
Callers decide *when* to use the sandbox; this library owns *how* the sandbox
actually contains the agent.

Public surface:
    manager              -- spawn-path API: wrap_command, ensure_image, etc.
    bypass_permissions_validator -- safety gate for BYPASS / DOCKER flags
    credential_patterns  -- high-confidence secret pattern detection
    workspace_delimiter  -- UNTRUSTED_WORKSPACE_FILE framing
    system_prompt        -- sandbox-aware system-prompt addendum
    tls_pin              -- TOFU certificate pinning for api.anthropic.com
    audit_log_shipping   -- external audit-log sink shipping
"""

from sandbox_core_lib.sandbox_core_lib.manager import (  # noqa: F401
    SANDBOX_IMAGE_TAG,
    SandboxError,
    docker_available,
    gvisor_runtime_available,
    docker_running_rootless,
    check_gvisor_or_exit,
    check_docker_or_exit,
    ensure_image,
    ensure_network,
    wrap_command,
    scan_workspace_for_secrets,
    enforce_no_workspace_secrets,
    make_container_name,
    check_spawn_rate,
    record_spawn,
    login_command,
    stamp_auth_volume_manifest,
    image_exists,
    image_built_by_kato,
    build_image,
    ALLOW_NO_GVISOR_ENV_KEY,
    ALLOW_WORKSPACE_SECRETS_ENV_KEY,
    AUDIT_REQUIRED_ENV_KEY,
    _REQUIRED_DOCKER_FLAGS,
    _FORBIDDEN_DOCKER_FLAGS,
    _AUTH_VOLUME_INVARIANTS,
    _FIREWALL_GUARANTEES,
    _CLASSIFICATION_TERMS,
)
from sandbox_core_lib.sandbox_core_lib.bypass_permissions_validator import (  # noqa: F401
    BYPASS_ENV_KEY,
    DOCKER_ENV_KEY,
    READ_ONLY_TOOLS_ENV_KEY,
    READ_ONLY_TOOLS_ALLOWLIST,
    BypassPermissionsRefused,
    is_bypass_enabled,
    is_docker_mode_enabled,
    is_read_only_tools_enabled,
    is_running_as_root,
    validate_bypass_permissions,
    validate_read_only_tools_requires_docker,
    print_security_posture,
)
from sandbox_core_lib.sandbox_core_lib.credential_patterns import (  # noqa: F401
    CredentialFinding,
    find_credential_patterns,
    find_phishing_patterns,
    summarize_findings,
)
from sandbox_core_lib.sandbox_core_lib.workspace_delimiter import (  # noqa: F401
    wrap_untrusted_workspace_content,
    OPEN_TAG,
    CLOSE_TAG,
)
from sandbox_core_lib.sandbox_core_lib.system_prompt import (  # noqa: F401
    compose_system_prompt,
    SANDBOX_SYSTEM_PROMPT_ADDENDUM,
    WORKSPACE_SCOPE_ADDENDUM,
    RESUMED_SESSION_ADDENDUM,
)
from sandbox_core_lib.sandbox_core_lib.tls_pin import (  # noqa: F401
    TlsPinError,
    is_pinning_enabled,
    validate_anthropic_tls_pin_or_refuse,
)
from sandbox_core_lib.sandbox_core_lib.audit_log_shipping import (  # noqa: F401
    AuditShipError,
    ship_audit_entry,
    is_shipping_enabled,
)

"""Declarative schema for every operator-editable env setting.

One source of truth the planning-UI Settings drawer renders from.
Each section becomes a tab; each field knows its type, help text,
and (for the dangerous ones) warnings. The webserver's
``/api/all-settings`` route serves this schema + the resolved
values and accepts writes for any key whose name appears here — the
schema IS the write whitelist, so a payload can't smuggle a key
the UI doesn't declare.

Field ``type``:
  * ``text``    — single-line string
  * ``secret``  — string rendered as a password input
  * ``number``  — integer / float (stored as a string in .env land)
  * ``bool``    — ``"true"`` / ``"false"``
  * ``select``  — one of ``options``

Provider + repo-root keys are deliberately NOT here — they have
dedicated tabs (Task provider / Git provider / Repositories) with
custom logic (active-platform switch, path validation). Everything
else in ``.env.example`` is covered below.
"""

from __future__ import annotations

LOG_LEVELS = ['debug', 'info', 'warning', 'error', 'critical']

# Each entry: (key, type, label, help, extra). ``extra`` is a dict:
#   options=[...]         for select
#   warning="..."         renders an amber inline warning
#   danger="..."          renders a red inline warning + needs the
#                          operator to tick a confirm box before the
#                          value can flip on (frontend-enforced)
#   placeholder="..."     input placeholder
SETTINGS_SCHEMA: list[dict] = [
    {
        'id': 'general',
        'label': 'General',
        'title': 'General',
        'description': 'Core orchestration knobs — backend, logging, '
                       'parallelism, workspace + discovery.',
        'fields': [
            ('KATO_AGENT_BACKEND', 'select', 'Agent backend',
             'Which agent runs implementation/testing/review work.',
             {'options': ['openhands', 'claude']}),
            ('KATO_LOG_LEVEL', 'select', 'Log level',
             'Root log verbosity.', {'options': LOG_LEVELS}),
            ('KATO_WORKFLOW_LOG_LEVEL', 'select', 'Workflow log level',
             'Verbosity for the task-workflow logger.',
             {'options': LOG_LEVELS}),
            ('KATO_EXTERNAL_API_MAX_RETRIES', 'number',
             'External API max retries',
             'Retries for ticket/git provider API calls.', {}),
            ('KATO_WORKSPACES_ROOT', 'text', 'Workspaces root',
             'Per-task clone folder. Empty = ~/.kato/workspaces.', {}),
            ('KATO_MAX_PARALLEL_TASKS', 'number', 'Max parallel tasks',
             'How many tasks execute concurrently (1 = sequential).',
             {}),
            ('KATO_IGNORED_REPOSITORY_FOLDERS', 'text',
             'Ignored repo folders',
             'Comma-separated folder names excluded from auto-discovery.',
             {}),
            ('KATO_REPOSITORY_DENYLIST', 'text', 'Repository denylist',
             'Comma-separated repo ids kato must NEVER touch '
             '(secrets-vault, regulated-data, …). Boot-time refusal.',
             {}),
            ('KATO_WEBSERVER_PORT', 'number', 'Webserver port',
             'Host port for the planning UI (Flask).', {}),
            ('KATO_TASK_PUBLISH_MAX_RETRIES', 'number',
             'Publish max retries',
             'Retries for the publish step (PR + move-to-review).', {}),
            ('KATO_WORKSPACE_REVIEW_TTL_SECONDS', 'number',
             'Workspace review TTL (s)',
             'How long a review-state workspace survives before '
             'cleanup. 0 = disable TTL cleanup.', {}),
            ('KATO_OPERATOR_EMAIL', 'text', 'Operator email',
             'Recorded as approved_by on approvals. Audit only.', {}),
            ('KATO_ARCHITECTURE_DOC_PATH', 'text',
             'Architecture doc path',
             'Markdown file appended to Claude\'s system prompt on '
             'every spawn. Re-read each spawn.', {}),
            ('KATO_APPROVED_REPOSITORIES_PATH', 'text',
             'Approvals sidecar path',
             'Override the approvals JSON location. Empty = '
             '~/.kato/approved-repositories.json.', {}),
        ],
    },
    {
        'id': 'claude_agent',
        'label': 'Claude agent',
        'title': 'Claude agent',
        'description': 'Used when Agent backend = claude. Auth: set '
                       'CLAUDE_CODE_OAUTH_TOKEN (Max/Pro) OR '
                       'ANTHROPIC_API_KEY (pay-per-token).',
        'fields': [
            ('KATO_CLAUDE_BINARY', 'text', 'Claude binary',
             'Path to the `claude` CLI. Plain `claude` works on PATH.',
             {}),
            ('KATO_CLAUDE_MODEL', 'text', 'Model override',
             'e.g. claude-opus-4-7. Empty = Claude Code default.', {}),
            ('KATO_CLAUDE_MAX_TURNS', 'number', 'Max turns',
             'Cap on agent turns per task. Empty = no cap.', {}),
            ('KATO_CLAUDE_EFFORT', 'select', 'Reasoning effort',
             'Passed via --effort. Higher = more tokens/time.',
             {'options': ['', 'low', 'medium', 'high', 'xhigh', 'max']}),
            ('KATO_CLAUDE_ALLOWED_TOOLS', 'text', 'Allowed tools',
             'Comma-separated --allowedTools. Empty → safe default '
             'when bypass is off.', {}),
            ('KATO_CLAUDE_DISALLOWED_TOOLS', 'text', 'Disallowed tools',
             'Comma-separated --disallowedTools.', {}),
            ('KATO_CLAUDE_TIMEOUT_SECONDS', 'number',
             'Per-task timeout (s)',
             'Subprocess timeout for the Claude CLI per task.', {}),
            ('KATO_CLAUDE_MODEL_SMOKE_TEST_ENABLED', 'bool',
             'Model smoke test',
             'Boot-time model-access check. Off by default (spend).',
             {}),
            ('ANTHROPIC_API_KEY', 'secret', 'Anthropic API key',
             'Pay-per-token auth. Use this OR the OAuth token.', {}),
            ('CLAUDE_CODE_OAUTH_TOKEN', 'secret',
             'Claude Code OAuth token',
             'Max/Pro plan token from `claude setup-token`. '
             'Recommended for Docker.', {}),
        ],
    },
    {
        'id': 'sandbox',
        'label': 'Sandbox',
        'title': 'Sandbox & permission bypass',
        'description': 'The containment + prompt layers. These change '
                       'how much the agent can do WITHOUT asking. Read '
                       'every warning — some of these make kato refuse '
                       'to boot in certain environments.',
        'fields': [
            ('KATO_CLAUDE_DOCKER', 'bool', 'Docker sandbox',
             'Wrap every Claude spawn in the hardened Docker sandbox '
             '(workspace bind-mount only, default-DROP egress '
             'firewall, capability drop, read-only rootfs, audit '
             'log). Independent of bypass.',
             {'warning': 'Requires a working Docker daemon. Kato '
                         'REFUSES to boot if this is true and Docker '
                         'is unavailable — it will not silently fall '
                         'back to host execution.'}),
            ('KATO_CLAUDE_ALLOWED_READ_ONLY_TOOLS', 'bool',
             'Pre-approve read-only tools',
             'Skip the per-tool prompt for a hardcoded read-only '
             'Bash allowlist (grep / cat / ls / find …).',
             {'warning': 'Requires Docker sandbox = true. Without the '
                         'sandbox even `grep` runs on the host and can '
                         'read SSH keys / any file you can read — kato '
                         'refuses at startup if this is on and Docker '
                         'is off.'}),
            ('KATO_CLAUDE_BYPASS_PERMISSIONS', 'bool',
             'Bypass ALL permission prompts',
             'The agent runs every tool (Bash, Edit, Write, …) with '
             'NO prompt, inside the Docker sandbox. See '
             'BYPASS_PROTECTIONS.md / SECURITY.md.',
             {'danger': 'DANGEROUS. Kato will REFUSE to start if: it '
                        'runs as root; Docker sandbox is off; the '
                        'environment is non-interactive (CI / cron / '
                        'systemd / Docker); or you answer "no" at '
                        'either startup confirmation prompt. When on, '
                        'kato writes an unmissable banner. Only enable '
                        'if you understand BYPASS_PROTECTIONS.md.'}),
        ],
    },
    {
        'id': 'security_scanner',
        'label': 'Security scanner',
        'title': 'Pre-execution security scanner',
        'description': 'Scans each task\'s workspace clone for '
                       'committed secrets / vulnerable deps / '
                       'dangerous patterns before the agent runs. '
                       'Blocks on CRITICAL by default.',
        'fields': [
            ('KATO_SECURITY_SCANNER_ENABLED', 'bool',
             'Scanner enabled',
             'Master switch. OFF is NOT recommended for teams that '
             'ship to production.',
             {'warning': 'Disabling removes the committed-secret / '
                         'vulnerable-dep gate entirely.'}),
            ('KATO_SECURITY_RUNNER_ENV_FILE', 'bool',
             'Runner: .env / secret scan', '', {}),
            ('KATO_SECURITY_RUNNER_DETECT_SECRETS', 'bool',
             'Runner: detect-secrets', '', {}),
            ('KATO_SECURITY_RUNNER_BANDIT', 'bool',
             'Runner: bandit (Python)', '', {}),
            ('KATO_SECURITY_RUNNER_SAFETY', 'bool',
             'Runner: safety (deps)', '', {}),
            ('KATO_SECURITY_RUNNER_NPM_AUDIT', 'bool',
             'Runner: npm-audit',
             'Off by default — noisy transitive-dep CVEs.', {}),
            ('KATO_SECURITY_TIMEOUT_SECRETS', 'number',
             'Timeout: secrets (s)', '', {}),
            ('KATO_SECURITY_TIMEOUT_DEPENDENCIES', 'number',
             'Timeout: dependencies (s)', '', {}),
            ('KATO_SECURITY_TIMEOUT_CODE_PATTERNS', 'number',
             'Timeout: code patterns (s)', '', {}),
        ],
    },
    {
        'id': 'email_slack',
        'label': 'Email & Slack',
        'title': 'Email & Slack notifications',
        'description': 'Server-side notifications kato sends on task '
                       'failure / completion. (Browser notifications '
                       'are the separate Notifications tab.)',
        'fields': [
            ('KATO_FAILURE_EMAIL_ENABLED', 'bool',
             'Failure email enabled', '', {}),
            ('KATO_FAILURE_EMAIL_TEMPLATE_ID', 'number',
             'Failure template id', '', {}),
            ('KATO_FAILURE_EMAIL_TO', 'text', 'Failure email to', '', {}),
            ('KATO_FAILURE_EMAIL_SENDER_NAME', 'text',
             'Failure sender name', '', {}),
            ('KATO_FAILURE_EMAIL_SENDER_EMAIL', 'text',
             'Failure sender email', '', {}),
            ('KATO_COMPLETION_EMAIL_ENABLED', 'bool',
             'Completion email enabled', '', {}),
            ('KATO_COMPLETION_EMAIL_TEMPLATE_ID', 'number',
             'Completion template id', '', {}),
            ('KATO_COMPLETION_EMAIL_TO', 'text',
             'Completion email to', '', {}),
            ('KATO_COMPLETION_EMAIL_SENDER_NAME', 'text',
             'Completion sender name', '', {}),
            ('KATO_COMPLETION_EMAIL_SENDER_EMAIL', 'text',
             'Completion sender email', '', {}),
            ('EMAIL_CORE_LIB_SEND_IN_BLUE_API_KEY', 'secret',
             'Brevo/SendinBlue API key', '', {}),
            ('SLACK_WEBHOOK_URL_ERRORS_EMAIL', 'secret',
             'Slack error webhook URL', '', {}),
        ],
    },
    {
        'id': 'openhands',
        'label': 'OpenHands',
        'title': 'OpenHands backend',
        'description': 'Used when Agent backend = openhands. Container, '
                       'LLM, scan-loop, and runtime config.',
        'fields': [
            ('OPENHANDS_BASE_URL', 'text', 'Base URL', '', {}),
            ('OPENHANDS_API_KEY', 'secret', 'API key', '', {}),
            ('OPENHANDS_SKIP_TESTING', 'bool', 'Skip testing', '', {}),
            ('OPENHANDS_TESTING_CONTAINER_ENABLED', 'bool',
             'Testing container enabled', '', {}),
            ('OPENHANDS_TESTING_BASE_URL', 'text',
             'Testing base URL', '', {}),
            ('OPENHANDS_TESTING_PORT', 'number', 'Testing port', '', {}),
            ('OPENHANDS_CONTAINER_LOG_ALL_EVENTS', 'bool',
             'Log all container events', '', {}),
            ('OPENHANDS_PORT', 'number', 'Container port', '', {}),
            ('OPENHANDS_PULL_POLICY', 'select', 'Pull policy', '',
             {'options': ['missing', 'always', 'never']}),
            ('OPENHANDS_LOG_LEVEL', 'select', 'Log level', '',
             {'options': LOG_LEVELS}),
            ('OH_SECRET_KEY', 'secret', 'OH secret key',
             'Stable random secret for OpenHands secret persistence.',
             {}),
            ('OPENHANDS_STATE_DIR', 'text', 'State dir', '', {}),
            ('OPENHANDS_WEB_URL', 'text', 'Web URL', '', {}),
            ('OPENHANDS_RUNTIME', 'text', 'Runtime', '', {}),
            ('OPENHANDS_SSH_AUTH_SOCK_HOST_PATH', 'text',
             'SSH auth sock host path', '', {}),
            ('OPENHANDS_LLM_MODEL', 'text', 'LLM model', '', {}),
            ('OPENHANDS_LLM_API_KEY', 'secret', 'LLM API key', '', {}),
            ('OPENHANDS_LLM_BASE_URL', 'text', 'LLM base URL', '', {}),
            ('OPENHANDS_MODEL_SMOKE_TEST_ENABLED', 'bool',
             'LLM smoke test', '', {}),
            ('OPENHANDS_TESTING_LLM_MODEL', 'text',
             'Testing LLM model', '', {}),
            ('OPENHANDS_TESTING_LLM_API_KEY', 'secret',
             'Testing LLM API key', '', {}),
            ('OPENHANDS_TESTING_LLM_BASE_URL', 'text',
             'Testing LLM base URL', '', {}),
            ('OPENHANDS_LLM_API_VERSION', 'text',
             'LLM API version', '', {}),
            ('OPENHANDS_LLM_NUM_RETRIES', 'number',
             'LLM num retries', '', {}),
            ('OPENHANDS_LLM_TIMEOUT', 'number', 'LLM timeout', '', {}),
            ('OPENHANDS_POLL_INTERVAL_SECONDS', 'number',
             'Poll interval (s)', '', {}),
            ('OPENHANDS_MAX_POLL_ATTEMPTS', 'number',
             'Max poll attempts', '', {}),
            ('OPENHANDS_TASK_SCAN_STARTUP_DELAY_SECONDS', 'number',
             'Scan startup delay (s)', '', {}),
            ('OPENHANDS_TASK_SCAN_INTERVAL_SECONDS', 'number',
             'Scan interval (s)', '', {}),
            ('OPENHANDS_LLM_DISABLE_VISION', 'text',
             'Disable vision', '', {}),
            ('OPENHANDS_LLM_DROP_PARAMS', 'text', 'Drop params', '', {}),
            ('OPENHANDS_LLM_CACHING_PROMPT', 'text',
             'Caching prompt', '', {}),
        ],
    },
    {
        'id': 'infra',
        'label': 'Docker / infra',
        'title': 'Docker & infrastructure',
        'description': 'Compose / image config for containerised runs.',
        'fields': [
            ('MOUNT_DOCKER_DATA_ROOT', 'text', 'Docker data root', '',
             {}),
            ('KATO_AGENT_SERVER_IMAGE_REPOSITORY', 'text',
             'Agent server image repo', '', {}),
            ('KATO_AGENT_SERVER_IMAGE_TAG', 'text',
             'Agent server image tag', '', {}),
        ],
    },
    {
        'id': 'aws',
        'label': 'AWS / Bedrock',
        'title': 'AWS / Bedrock',
        'description': 'Optional — only for Bedrock-backed LLM setups.',
        'fields': [
            ('AWS_ACCESS_KEY_ID', 'text', 'Access key id', '', {}),
            ('AWS_SECRET_ACCESS_KEY', 'secret', 'Secret access key',
             '', {}),
            ('AWS_REGION_NAME', 'text', 'Region', '', {}),
            ('AWS_SESSION_TOKEN', 'secret', 'Session token', '', {}),
            ('AWS_BEARER_TOKEN_BEDROCK', 'secret',
             'Bedrock bearer token', '', {}),
        ],
    },
]


def all_settings_keys() -> set[str]:
    """Every key the generic settings route may write — the whitelist."""
    keys: set[str] = set()
    for section in SETTINGS_SCHEMA:
        for field in section['fields']:
            keys.add(field[0])
    return keys


def schema_for_api() -> list[dict]:
    """JSON-serialisable schema (tuples → dicts) for the GET response."""
    out = []
    for section in SETTINGS_SCHEMA:
        fields = []
        for key, ftype, label, help_text, extra in section['fields']:
            entry = {
                'key': key,
                'type': ftype,
                'label': label,
                'help': help_text,
            }
            entry.update(extra or {})
            fields.append(entry)
        out.append({
            'id': section['id'],
            'label': section['label'],
            'title': section['title'],
            'description': section['description'],
            'fields': fields,
        })
    return out

"""Action Guard — classify an agent tool call into a risk category + decision.

The agent transports run the model's tool calls (Bash, Edit, Write, Read…)
on a real machine. A coding agent that "reads a codebase and fixes flaws"
can, by accident or prompt-injection, *attempt* host-harming actions: wipe
a directory, read ``~/.ssh`` and exfiltrate it, ``curl … | sh`` a remote
script, install a persistence backdoor, pop a reverse shell. Antivirus
flags exactly these.

This module is the pure, product-agnostic engine that looks at one tool
call and returns a :class:`GuardVerdict` — a ``RiskCategory`` plus a
``Decision`` (BLOCK / ASK / ALLOW). The CALLER (the permission path in the
transport / webserver) enforces the decision: BLOCK denies the tool and
tells the agent why; ASK routes it to the operator's approval modal; ALLOW
passes through.

Design rules (mirrors ``sandbox_scope`` / ``credential_patterns``):
* **Pure + filesystem-free + deterministic.** Patterns run on a
  de-obfuscated copy of the command (see ``command_introspection``) and
  per ``&&``/``;``/``|`` segment, so ``safe && rm -rf /`` is caught.
* **Reuse, don't reinvent.** The remote-exec patterns come from
  ``credential_patterns._PHISHING_PATTERNS``; the escape classifier and
  the de-obfuscator come from ``command_introspection``; the workspace
  out-of-scope classifiers are *injected* by the transport (they encode
  Claude's per-task workspace layout and live in ``claude_core_lib``).
* **Secure defaults, operator-tunable, with a non-loosenable floor.** A
  no-legit-use detection (reverse shell, fork bomb, ``mkfs``, ``dd
  of=/dev/``, sandbox escape) ALWAYS blocks regardless of policy. Dual-use
  actions (a plain ``rm -rf build/``, ``sudo``, a write to ``~/.bashrc``)
  default to ASK so the operator decides without breaking legitimate work.
* **Ambiguity favors ASK over BLOCK**, and a classifier crash must never
  break the permission pipeline — the caller wraps this fail-open, with
  Layer-A (the CLI ``--disallowedTools`` floor) and Docker as backstops.

Static analysis only: a path/command built at RUNTIME from ``$VAR``,
base64, or fetched data is invisible here — OS-level confinement
(KATO_CLAUDE_DOCKER) is the structural guarantee for those.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from agent_core_lib.agent_core_lib.helpers.command_introspection import (
    classify_command_escape,
    deobfuscate_command,
    split_command_segments,
)
from agent_core_lib.agent_core_lib.helpers.credential_patterns import (
    _PHISHING_PATTERNS,
)


class RiskCategory(str, Enum):
    """The kind of risk a tool call presents. All but ``NONE`` are
    operator-configurable; ``NONE`` means no detector fired."""

    DESTRUCTIVE_FS = 'destructive_fs'
    CREDENTIAL_READ = 'credential_read'
    NETWORK_EXFIL = 'network_exfil'
    REMOTE_EXEC = 'remote_exec'
    PERSISTENCE = 'persistence'
    PRIV_ESC = 'priv_esc'
    SANDBOX_ESCAPE = 'sandbox_escape'
    OUT_OF_SCOPE = 'out_of_scope'
    # A tool that reaches the network / a third-party service (WebFetch,
    # WebSearch, any MCP connector). Off-machine data flow → BLOCK by default.
    NETWORK_TOOL = 'network_tool'
    # A tool Kato does not recognize as a known-safe local tool — e.g. a NEW
    # Claude capability. Default-deny-by-asking so every new capability needs
    # explicit operator approval.
    EXTERNAL_CAPABILITY = 'external_capability'
    NONE = 'none'


class Decision(str, Enum):
    BLOCK = 'block'
    ASK = 'ask'
    ALLOW = 'allow'


# The categories the operator can tune, in the order shown in the UI.
CONFIGURABLE_CATEGORIES: tuple[RiskCategory, ...] = (
    RiskCategory.DESTRUCTIVE_FS,
    RiskCategory.CREDENTIAL_READ,
    RiskCategory.NETWORK_EXFIL,
    RiskCategory.REMOTE_EXEC,
    RiskCategory.PERSISTENCE,
    RiskCategory.PRIV_ESC,
    RiskCategory.SANDBOX_ESCAPE,
    RiskCategory.OUT_OF_SCOPE,
    RiskCategory.NETWORK_TOOL,
    RiskCategory.EXTERNAL_CAPABILITY,
)

# Whole categories with NO legitimate use in a coding agent: an operator
# may never downgrade these below BLOCK (``from_mapping`` clamps them).
_FLOOR_CATEGORIES: frozenset[RiskCategory] = frozenset({
    RiskCategory.REMOTE_EXEC,
    RiskCategory.SANDBOX_ESCAPE,
})

# Balanced-secure defaults: catastrophic + credential-read + exfil BLOCK
# (these are the antivirus-triggering patterns); dual-use defaults to ASK.
_SECURE_DEFAULTS: dict[RiskCategory, Decision] = {
    RiskCategory.DESTRUCTIVE_FS: Decision.ASK,   # catastrophic rm/mkfs/dd is a floor BLOCK
    RiskCategory.CREDENTIAL_READ: Decision.BLOCK,
    RiskCategory.NETWORK_EXFIL: Decision.BLOCK,
    RiskCategory.REMOTE_EXEC: Decision.BLOCK,
    RiskCategory.PERSISTENCE: Decision.ASK,
    RiskCategory.PRIV_ESC: Decision.ASK,
    RiskCategory.SANDBOX_ESCAPE: Decision.BLOCK,
    RiskCategory.OUT_OF_SCOPE: Decision.ASK,
    RiskCategory.NETWORK_TOOL: Decision.BLOCK,       # off-machine data flow
    RiskCategory.EXTERNAL_CAPABILITY: Decision.ASK,  # new/unknown capability
}

_DECISION_SEVERITY: dict[Decision, int] = {
    Decision.ALLOW: 0,
    Decision.ASK: 1,
    Decision.BLOCK: 2,
}


@dataclass(frozen=True)
class GuardVerdict(object):
    """The engine's ruling on one tool call."""

    category: RiskCategory
    decision: Decision
    reason: str         # operator/agent-readable, already redacted
    rule_id: str        # stable id of the matched rule, for the audit log


@dataclass(frozen=True)
class _Candidate(object):
    """One detector hit, before the policy turns it into a decision."""

    category: RiskCategory
    is_floor: bool      # True → BLOCK regardless of operator policy
    reason: str
    rule_id: str


def _coerce_decision(value) -> Decision | None:
    if isinstance(value, Decision):
        return value
    try:
        return Decision(str(value).strip().lower())
    except (ValueError, AttributeError):
        return None


def _coerce_category(value) -> RiskCategory | None:
    if isinstance(value, RiskCategory):
        return value if value in CONFIGURABLE_CATEGORIES else None
    try:
        category = RiskCategory(str(value).strip().lower())
    except (ValueError, AttributeError):
        return None
    return category if category in CONFIGURABLE_CATEGORIES else None


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


@dataclass(frozen=True)
class CommandPolicy(object):
    """Operator-resolved posture: a decision per configurable category plus
    a master enable switch. Build with :meth:`secure_default` /
    :meth:`from_mapping`; never mutate (it is shared across tool calls)."""

    decisions: dict
    enabled: bool = True

    @classmethod
    def secure_default(cls) -> 'CommandPolicy':
        return cls(decisions=dict(_SECURE_DEFAULTS), enabled=True)

    @classmethod
    def from_mapping(cls, raw, *, base: 'CommandPolicy' | None = None) -> 'CommandPolicy':
        """Layer an operator override mapping over a base (secure by default).

        ``raw`` maps a category (``RiskCategory`` or its string value) to a
        decision string. An ``enabled`` key toggles the master switch.
        Floor categories cannot be loosened below BLOCK — an attempt is
        silently clamped (the boot banner surfaces the resolved posture).
        Unknown keys/values are ignored so a typo can never crash the guard.
        """
        base = base or cls.secure_default()
        decisions = dict(base.decisions)
        enabled = base.enabled
        for key, value in dict(raw or {}).items():
            if isinstance(key, str) and key.strip().lower() == 'enabled':
                enabled = _coerce_bool(value)
                continue
            category = _coerce_category(key)
            decision = _coerce_decision(value)
            if category is None or decision is None:
                continue
            if category in _FLOOR_CATEGORIES and decision is not Decision.BLOCK:
                decision = Decision.BLOCK
            decisions[category] = decision
        return cls(decisions=decisions, enabled=enabled)

    def decide(self, category: RiskCategory) -> Decision:
        return self.decisions.get(category, Decision.ALLOW)


# --------------------------------------------------------------------------
# Detectors. Each runs on the de-obfuscated command (or a path argument) and
# appends zero or more candidates. Patterns are deliberately high-signal:
# they target the dangerous shape, not merely the program name.
# --------------------------------------------------------------------------

# Tool-input keys that name a filesystem path (mirrors sandbox_scope; the
# 4-name tuple is too small to be worth a cross-lib import).
_PATH_KEYS = ('file_path', 'notebook_path', 'path', 'file')

# --- destructive filesystem -----------------------------------------------
_CATASTROPHIC_RM_TARGET = re.compile(r'^(/|/\*|~|~/|~/\*|\*|\.\.?/?\*?)$')
_FORK_BOMB = re.compile(r':\s*\(\s*\)\s*\{[^}]*\|[^}]*&[^}]*\}\s*;\s*:')
_MKFS = re.compile(r'\bmkfs(\.\w+)?\b')
_DD_TO_DEVICE = re.compile(r'\bdd\b[^|;&]*\bof=\s*/dev/')
_REDIRECT_TO_DEVICE = re.compile(r'>\s*/dev/(sd|nvme|hd|vd|disk|mmcblk)')
_SHRED_DEVICE = re.compile(r'\bshred\b[^|;&]*/dev/')


def _rm_analysis(segment: str):
    """For a segment invoking ``rm``, return (recursive, force, targets)."""
    tokens = [t for t in segment.strip().split() if t]
    index = next(
        (i for i, t in enumerate(tokens) if t.rsplit('/', 1)[-1] == 'rm'), -1,
    )
    if index < 0:
        return None
    rest = tokens[index + 1:]
    short = ''.join(t[1:] for t in rest if t.startswith('-') and not t.startswith('--'))
    longs = [t for t in rest if t.startswith('--')]
    recursive = 'r' in short or 'R' in short or '--recursive' in longs
    force = 'f' in short or '--force' in longs
    no_preserve = '--no-preserve-root' in longs
    targets = [t for t in rest if not t.startswith('-')]
    return recursive, force, no_preserve, targets


def _detect_destructive_fs(command: str, segments) -> list:
    found: list = []
    if _FORK_BOMB.search(command):
        found.append(_Candidate(
            RiskCategory.DESTRUCTIVE_FS, True,
            'fork bomb — would exhaust the machine', 'fs.fork_bomb',
        ))
    if _MKFS.search(command):
        found.append(_Candidate(
            RiskCategory.DESTRUCTIVE_FS, True,
            'mkfs would format a filesystem', 'fs.mkfs',
        ))
    if _DD_TO_DEVICE.search(command) or _REDIRECT_TO_DEVICE.search(command) \
            or _SHRED_DEVICE.search(command):
        found.append(_Candidate(
            RiskCategory.DESTRUCTIVE_FS, True,
            'writes directly to a block device', 'fs.device_write',
        ))
    for segment in segments:
        analysis = _rm_analysis(segment)
        if not analysis:
            continue
        recursive, force, no_preserve, targets = analysis
        if not recursive:
            continue
        catastrophic = no_preserve or any(
            _CATASTROPHIC_RM_TARGET.match(t) for t in targets
        )
        if catastrophic:
            found.append(_Candidate(
                RiskCategory.DESTRUCTIVE_FS, True,
                'recursive delete of a root/home path', 'fs.rm_root',
            ))
        elif force:
            found.append(_Candidate(
                RiskCategory.DESTRUCTIVE_FS, False,
                'recursive force-delete', 'fs.rm_recursive_force',
            ))
    if re.search(r'\bfind\b[^|;&]*\s-delete\b', command):
        found.append(_Candidate(
            RiskCategory.DESTRUCTIVE_FS, False,
            'find … -delete removes matched files', 'fs.find_delete',
        ))
    if re.search(r'\bchmod\b[^|;&]*\s-\w*R\w*[^|;&]*\b(000|777)\b', command):
        found.append(_Candidate(
            RiskCategory.DESTRUCTIVE_FS, False,
            'recursive chmod to a dangerous mode', 'fs.chmod_recursive',
        ))
    return found


# --- remote code execution (floor) ----------------------------------------
_REMOTE_EXEC_PHISHING = frozenset({'pipe_to_shell', 'eval_remote_fetch'})
_SOURCE_PROCESS_SUB = re.compile(r'(?:\bsource\b|(?:^|[\s;&|])\.)\s+<\(\s*(?:curl|wget)\b')


def _detect_remote_exec(command: str) -> list:
    for name, regex in _PHISHING_PATTERNS:
        if name in _REMOTE_EXEC_PHISHING and regex.search(command):
            return [_Candidate(
                RiskCategory.REMOTE_EXEC, True,
                'pipes a downloaded script straight into a shell', f'rce.{name}',
            )]
    if _SOURCE_PROCESS_SUB.search(command):
        return [_Candidate(
            RiskCategory.REMOTE_EXEC, True,
            'sources a remote script via process substitution', 'rce.source_proc_sub',
        )]
    return []


# --- network exfiltration / reverse shells --------------------------------
_REVERSE_SHELL_DEV = re.compile(r'/dev/(tcp|udp)/')
_NC_EXEC = re.compile(r'\b(nc|ncat)\b[^|;&]*\s-\w*e\b')
_SOCAT_EXEC = re.compile(r'\bsocat\b[^|;&]*\b(exec|system):')
_CURL_UPLOAD = re.compile(
    r'\bcurl\b[^|;&]*(?:\s-T\b|\s--upload-file\b|\s-d\s*@|'
    r'\s--data(?:-binary|-raw|-ascii)?\s*@|\s-F\s*\S*=@|\s--form\s*\S*=@)',
)
_WGET_POST = re.compile(r'\bwget\b[^|;&]*--post-file\b')
_SCP_SFTP = re.compile(r'\b(scp|sftp)\b\s')
_RSYNC_REMOTE = re.compile(r'\brsync\b[^|;&]*\s\S*\w@?[\w.-]*:')
_NC_CONNECT = re.compile(r'\b(nc|ncat)\b\s+\S+\s+\d{1,5}\b')


def _detect_network_exfil(command: str) -> list:
    if _REVERSE_SHELL_DEV.search(command) or _NC_EXEC.search(command) \
            or _SOCAT_EXEC.search(command):
        return [_Candidate(
            RiskCategory.NETWORK_EXFIL, True,
            'opens a reverse shell / raw network connection', 'net.reverse_shell',
        )]
    if _CURL_UPLOAD.search(command) or _WGET_POST.search(command):
        return [_Candidate(
            RiskCategory.NETWORK_EXFIL, False,
            'uploads a local file to a remote host', 'net.http_upload',
        )]
    if _SCP_SFTP.search(command) or _RSYNC_REMOTE.search(command) \
            or _NC_CONNECT.search(command):
        return [_Candidate(
            RiskCategory.NETWORK_EXFIL, False,
            'transfers data to a remote host', 'net.remote_copy',
        )]
    return []


# --- credential reads ------------------------------------------------------
_CRED_PATHS = (
    ('cred.ssh', re.compile(r'(?:^|/|~)\.ssh(?:/|$)')),
    ('cred.ssh_private_key', re.compile(r'\bid_(rsa|ed25519|ecdsa|dsa)\b')),
    ('cred.aws', re.compile(r'(?:^|/|~)\.aws(?:/|$)')),
    ('cred.gcloud', re.compile(r'(?:^|/|~)\.config/gcloud(?:/|$)')),
    ('cred.kube', re.compile(r'(?:^|/|~)\.kube/config\b')),
    ('cred.netrc', re.compile(r'(?:^|/|~)\.netrc\b')),
    ('cred.docker', re.compile(r'(?:^|/|~)\.docker/config\.json\b')),
    ('cred.gnupg', re.compile(r'(?:^|/|~)\.gnupg(?:/|$)')),
    ('cred.gh', re.compile(r'(?:^|/|~)\.config/gh(?:/|$)')),
)
_CRED_FLOOR_PATHS = (
    ('cred.etc_shadow', re.compile(r'/etc/shadow\b')),
    ('cred.etc_sudoers', re.compile(r'/etc/sudoers\b')),
    ('cred.keychain_dump', re.compile(
        r'\bsecurity\s+(find-(generic|internet)-password|dump-keychain)\b')),
    ('cred.keychain_file', re.compile(r'\.keychain(-db)?\b')),
)


def _detect_credential_read(text: str) -> list:
    for rule_id, regex in _CRED_FLOOR_PATHS:
        if regex.search(text):
            return [_Candidate(
                RiskCategory.CREDENTIAL_READ, True,
                'reads system credential store', rule_id,
            )]
    for rule_id, regex in _CRED_PATHS:
        if regex.search(text):
            return [_Candidate(
                RiskCategory.CREDENTIAL_READ, False,
                'accesses a credential / secret file', rule_id,
            )]
    return []


# --- persistence -----------------------------------------------------------
_PERSIST_PATHS = (
    ('persist.shell_rc', re.compile(
        r'(?:^|/|~)\.(bashrc|zshrc|bash_profile|zprofile|profile|bash_login|zshenv|zlogin)\b')),
    ('persist.cron', re.compile(r'/etc/cron')),
    ('persist.launch', re.compile(r'/Library/Launch(Agents|Daemons)\b')),
    ('persist.systemd', re.compile(r'/(etc|lib|\.config)/systemd\b')),
    ('persist.rc_local', re.compile(r'/etc/rc\.local\b')),
)
_PERSIST_AUTHORIZED_KEYS = re.compile(r'authorized_keys\b')
_WRITE_INDICATOR = re.compile(r'(>>?|\btee\b|\bsed\b[^|;&]*\s-i|\bcp\b|\bmv\b|\bln\b|\binstall\b)')
_CRONTAB_INSTALL = re.compile(r'\bcrontab\b\s+-(?:\s|$)')   # crontab - (from stdin)
_CRONTAB_EDIT = re.compile(r'\bcrontab\b\s+(?:-e\b|[^-\s]\S*)')
_LAUNCHCTL_LOAD = re.compile(r'\blaunchctl\s+(load|bootstrap)\b')


def _detect_persistence_command(command: str) -> list:
    writes = bool(_WRITE_INDICATOR.search(command))
    if _PERSIST_AUTHORIZED_KEYS.search(command) and writes:
        return [_Candidate(
            RiskCategory.PERSISTENCE, True,
            'writes an SSH authorized_keys entry (backdoor)', 'persist.authorized_keys',
        )]
    if _CRONTAB_INSTALL.search(command):
        return [_Candidate(
            RiskCategory.PERSISTENCE, True,
            'installs a crontab from stdin', 'persist.crontab_stdin',
        )]
    if _CRONTAB_EDIT.search(command) or _LAUNCHCTL_LOAD.search(command):
        return [_Candidate(
            RiskCategory.PERSISTENCE, False,
            'registers a scheduled / launch job', 'persist.scheduler',
        )]
    if writes:
        for rule_id, regex in _PERSIST_PATHS:
            if regex.search(command):
                return [_Candidate(
                    RiskCategory.PERSISTENCE, False,
                    'modifies a shell/login startup file', rule_id,
                )]
    return []


def _detect_persistence_path(path_text: str) -> list:
    # A Write/Edit to a path is inherently a write — no write-indicator needed.
    if _PERSIST_AUTHORIZED_KEYS.search(path_text):
        return [_Candidate(
            RiskCategory.PERSISTENCE, True,
            'writes an SSH authorized_keys entry (backdoor)', 'persist.authorized_keys',
        )]
    for rule_id, regex in _PERSIST_PATHS:
        if regex.search(path_text):
            return [_Candidate(
                RiskCategory.PERSISTENCE, False,
                'modifies a shell/login startup file', rule_id,
            )]
    return []


# --- privilege escalation / sandbox escape --------------------------------
_SANDBOX_ESCAPE_PROGRAMS = frozenset({'nsenter', 'unshare', 'chroot'})


def _detect_escape(command: str) -> list:
    escapes, program = classify_command_escape(command)
    if not escapes:
        return []
    if program in _SANDBOX_ESCAPE_PROGRAMS:
        return [_Candidate(
            RiskCategory.SANDBOX_ESCAPE, True,
            f'{program} steps around the workspace sandbox', f'escape.{program}',
        )]
    return [_Candidate(
        RiskCategory.PRIV_ESC, False,
        f'{program} runs with elevated / host privileges', f'privesc.{program}',
    )]


# Tools that WRITE a path (so a persistence-path match is a real backdoor).
# A read-only tool (Read/Grep/Glob) touching ``~/.zshrc`` is not persistence.
_WRITE_TOOLS = frozenset({'write', 'edit', 'multiedit', 'notebookedit'})

# Known-safe LOCAL tools — operate on the workspace/filesystem, no network.
# Anything NOT here is treated as new/unknown (default-deny-by-asking) so a
# NEW Claude capability can never run silently. Lower-cased for matching.
_KNOWN_LOCAL_TOOLS = frozenset({
    'bash', 'edit', 'write', 'read', 'glob', 'grep',
    'multiedit', 'notebookedit', 'notebookread', 'todowrite',
    # Subagent fan-out (``Task`` renamed to ``Agent`` in CLI 2.1.63) — a
    # bounded local capability, not a new/external one.
    'agent', 'task',
})
# Tools that reach the network / a third-party service. ``mcp__*`` (any MCP
# connector) is matched by prefix. Off-machine data flow → BLOCK by default.
_NETWORK_TOOLS = frozenset({'webfetch', 'websearch'})


def _detect_tool_capability(tool_name: str) -> list:
    """Classify the TOOL ITSELF (independent of its arguments): a known-safe
    local tool is fine; a network/connector tool is off-machine data flow; an
    unrecognized tool is a new capability that must be approved."""
    name = str(tool_name or '').strip()
    if not name:
        return []
    lower = name.lower()
    if lower in _KNOWN_LOCAL_TOOLS:
        return []
    if lower.startswith('mcp__') or lower in _NETWORK_TOOLS:
        return [_Candidate(
            RiskCategory.NETWORK_TOOL, False,
            f'{name} reaches the network / a third-party service',
            f'tool.network.{lower}',
        )]
    return [_Candidate(
        RiskCategory.EXTERNAL_CAPABILITY, False,
        f'{name} is a new capability Kato does not recognize — approve it '
        'explicitly',
        f'tool.unknown.{lower}',
    )]


def _candidate_path_args(tool_input: dict) -> list:
    paths = []
    for key in _PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            paths.append(value.strip())
    return paths


def _out_of_scope_candidate(classifier, subject, cwd, additional_dirs,
                            allowed_paths, rule_id) -> list:
    if classifier is None:
        return []
    outside, offending = classifier(subject, cwd, additional_dirs, allowed_paths)
    if not outside:
        return []
    return [_Candidate(
        RiskCategory.OUT_OF_SCOPE, False,
        f'path outside the workspace: {offending}', rule_id,
    )]


def _detect_in_command(command, cwd, additional_dirs, allowed_paths,
                       command_sandbox_classifier) -> list:
    deob = deobfuscate_command(command)
    segments = split_command_segments(deob)
    # Order = severity priority (earlier wins ties among equal decisions).
    candidates: list = []
    candidates += _detect_network_exfil(deob)
    candidates += _detect_remote_exec(deob)
    candidates += _detect_escape(command)
    candidates += _detect_destructive_fs(deob, segments)
    candidates += _detect_credential_read(deob)
    candidates += _detect_persistence_command(deob)
    candidates += _out_of_scope_candidate(
        command_sandbox_classifier, command, cwd, additional_dirs,
        allowed_paths, 'scope.command',
    )
    return candidates


def _detect_in_paths(tool_name, tool_input, cwd, additional_dirs, allowed_paths,
                     tool_input_sandbox_classifier) -> list:
    is_write = (tool_name or '').strip().lower() in _WRITE_TOOLS
    candidates: list = []
    for raw_path in _candidate_path_args(tool_input):
        deob_path = deobfuscate_command(raw_path)
        candidates += _detect_credential_read(deob_path)
        if is_write:
            candidates += _detect_persistence_path(deob_path)
    candidates += _out_of_scope_candidate(
        tool_input_sandbox_classifier, tool_input, cwd, additional_dirs,
        allowed_paths, 'scope.path',
    )
    return candidates


def _resolve(candidates, policy) -> GuardVerdict:
    best_decision = None
    best_candidate = None
    for candidate in candidates:
        decision = (
            Decision.BLOCK if candidate.is_floor
            else policy.decide(candidate.category)
        )
        if best_decision is None \
                or _DECISION_SEVERITY[decision] > _DECISION_SEVERITY[best_decision]:
            best_decision = decision
            best_candidate = candidate
    if best_candidate is None:
        return GuardVerdict(RiskCategory.NONE, Decision.ALLOW, '', '')
    return GuardVerdict(
        best_candidate.category, best_decision,
        best_candidate.reason, best_candidate.rule_id,
    )


def classify_action(
    tool_name: str,
    tool_input,
    *,
    cwd: str = '',
    additional_dirs: tuple = (),
    allowed_paths: tuple = (),
    policy: CommandPolicy,
    command_sandbox_classifier=None,
    tool_input_sandbox_classifier=None,
) -> GuardVerdict:
    """Return the :class:`GuardVerdict` for one tool call.

    ``command_sandbox_classifier`` / ``tool_input_sandbox_classifier`` are the
    workspace out-of-scope classifiers from ``claude_core_lib.sandbox_scope``,
    injected so this engine stays free of the transport's workspace layout.
    When omitted, out-of-scope detection is simply skipped.

    The most-severe matched decision wins; ties favor the earlier (more
    dangerous) category. When nothing matches, the verdict is ALLOW/NONE.
    """
    if policy is None or not policy.enabled:
        return GuardVerdict(RiskCategory.NONE, Decision.ALLOW, '', '')

    tool_input = tool_input if isinstance(tool_input, dict) else {}
    command = str(tool_input.get('command') or '')
    # The tool itself first — a network/connector or unrecognized tool is
    # flagged no matter what arguments it carries (catches NEW capabilities).
    candidates = _detect_tool_capability(tool_name)
    if command.strip():
        candidates += _detect_in_command(
            command, cwd, additional_dirs, allowed_paths,
            command_sandbox_classifier,
        )
    else:
        candidates += _detect_in_paths(
            tool_name, tool_input, cwd, additional_dirs, allowed_paths,
            tool_input_sandbox_classifier,
        )
    return _resolve(candidates, policy)

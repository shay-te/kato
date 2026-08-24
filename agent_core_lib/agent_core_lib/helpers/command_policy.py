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
(the host Docker sandbox) is the structural guarantee for those.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from enum import Enum

from agent_core_lib.agent_core_lib.helpers.command_introspection import (
    classify_command_escape,
    deobfuscate_command,
    program_token_index,
    segment_program,
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
    # WebSearch, any MCP connector). Dual-use: legitimate research, but an
    # exfil vector — so ASK by default (operator approves; approval can be
    # remembered). The dangerous upload/reverse-shell patterns are
    # NETWORK_EXFIL, which stays BLOCK.
    NETWORK_TOOL = 'network_tool'
    # A tool the host does not recognize as a known-safe local tool — e.g. a NEW
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
    RiskCategory.NETWORK_TOOL: Decision.ASK,         # WebFetch/WebSearch — dual-use research tool
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
# ``~[\w-]*`` (not bare ``~``) also catches tilde-username targets
# (``~root``, ``~ubuntu``) — POSIX expands those to another user's home,
# just as catastrophic to recursively force-delete as your own.
_CATASTROPHIC_RM_TARGET = re.compile(
    r'^(/|/\*|~[\w-]*(?:/\*?)?|\*|\.\.?/?\*?)$'
)
# Repeated leading slashes (``//``, ``////``) are POSIX-identical to a
# single ``/`` for every path EXCEPT exactly ``//`` (implementation-defined,
# and no real filesystem treats it specially in practice) — collapse before
# matching so ``rm -rf //`` can't dodge the single-``/`` regex above.
_REPEATED_SLASHES = re.compile(r'/{2,}')


def _collapsed_slashes(target: str) -> str:
    return _REPEATED_SLASHES.sub('/', target)
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


# --- git working-tree reverts ---------------------------------------------
# ``git restore`` / ``git checkout -- <path>`` discard uncommitted changes.
# In an orchestrated task that is sharper than it sounds: the agent's work is
# NOT committed until the publish step, so the working tree IS the
# deliverable. Reverting the one file the operator asked about is routine;
# reverting the WHOLE tree destroys the entire task, and ``git reflog`` does
# not help because nothing was ever committed.
#
# So this classifier splits on PATHSPEC BREADTH, not on the subcommand:
#   git restore src/a.js     → scoped, no candidate (normal permission flow)
#   git restore .            → whole tree, ASK
#   git checkout -- ':/'     → whole tree, ASK
# NOT floor severity: "undo everything you did" is a legitimate thing for an
# operator to approve — it just must never happen without them seeing it.
# The breadth test is an ALLOWLIST, and that direction is the whole point.
#
# The first version of this rule enumerated the pathspecs that mean
# "everything" (``.``, ``*``, ``:/``, ``:(top)``) and treated the rest as
# scoped. That inverts the burden of proof against an open-ended grammar and
# it did not survive contact: git's magic-pathspec syntax alone supplies
# ``:!x`` / ``:(exclude)x`` (an exclude-only pathspec matches the WHOLE tree),
# ``:(glob)**``, ``:(attr:!binary)`` and a bare ``:`` — every one of which
# reverted an entire scratch repo while being classified ALLOW.
#
# So: a pathspec is scoped only if it is demonstrably a plain narrow path.
# Anything else — magic prefix, glob, traversal, runtime-built, or a pathspec
# set we cannot read at all — counts as whole-tree and goes to the operator.
# Unknown means ASK, never ALLOW.
_GIT_GLOB_CHARS = '*?['
# Options that take their value as the FOLLOWING token, so the value is not a
# pathspec. Missing one used to be exploitable in the opposite direction too:
# ``git --work-tree . restore .`` slipped past a regex that only modelled
# ``-c``/``-C`` and ``--flag=value``.
_GIT_PRECOMMAND_VALUE_OPTIONS = frozenset({
    '-c', '-C', '--git-dir', '--work-tree', '--namespace', '--exec-path',
    '--super-prefix', '--config-env',
})
_GIT_REVERT_VALUE_OPTIONS = frozenset({'--source', '-s'})
# We cannot read the file, so we cannot know the breadth — fail closed.
_GIT_PATHSPEC_FROM_FILE = '--pathspec-from-file'
_SHELL_PROGRAMS = frozenset({'sh', 'bash', 'zsh', 'dash', 'ksh', 'busybox', 'eval'})


def _git_tokens(segment: str) -> list:
    """Tokenize a segment for git parsing: quotes stripped, comment dropped.

    Shell grouping punctuation is trimmed off the ends so ``(git restore .)``
    — which ``split_command_segments`` does not break apart — parses as the
    command it is rather than yielding the pathspec ``.)``.
    """
    text = str(segment or '').strip()
    tokens = [t for t in text.split() if t]
    if tokens:
        tokens[0] = tokens[0].lstrip('(){}')
        tokens[-1] = tokens[-1].rstrip('){};')
        tokens = [t for t in tokens if t]
    cleaned: list = []
    for token in tokens:
        # A trailing shell comment is not an argument. Without this,
        # ``git restore --pathspec-from-file=x # revert everything`` handed
        # three fake "pathspecs" to the breadth test and read as scoped.
        if token.startswith('#'):
            break
        cleaned.append(token.strip('\'"'))
    return cleaned


def _is_narrow_pathspec(raw: str) -> bool:
    """True only for a plain path naming something narrower than the tree."""
    text = str(raw or '').strip().strip('\'"').strip()
    if not text:
        return False
    # Magic pathspec grammar (``:!x``, ``:(exclude)x``, ``:(glob)**``, ``:/``,
    # a bare ``:``) — repo-root anchored and/or whole-tree matching.
    if text.startswith(':'):
        return False
    # Built at runtime — invisible to static analysis.
    if '$' in text or '`' in text:
        return False
    if any(char in text for char in _GIT_GLOB_CHARS):
        return False
    if text.startswith('~'):
        return False
    normalized = posixpath.normpath(text.replace('\\', '/'))
    if normalized in ('.', '..', '/', ''):
        return False
    return not normalized.startswith('../')


def _git_subcommand_head(segment: str):
    """``(subcommand, remaining_tokens)`` for a git invocation, else ``None``.

    Anchored on the PROGRAM, not on a substring: ``echo "run git restore ."``
    and ``grep -rn "git restore ." docs/`` merely mention the command, and a
    substring match routed both to the approval modal — noise that trains an
    operator to click through the popup that matters.
    """
    tokens = _git_tokens(segment)
    if not tokens:
        return None
    index = program_token_index(tokens)
    if index >= len(tokens):
        return None
    program = tokens[index].rsplit('/', 1)[-1]
    # ``sh -c '…'`` / ``bash -c '…'`` wrap the real command in an argument;
    # ``eval …`` takes the payload as its plain arguments, with no ``-c``.
    # Missing that second shape is not academic — ``eval "git push"`` is
    # three characters of shell around a command the floor denies.
    if program in _SHELL_PROGRAMS:
        for position in range(index + 1, len(tokens)):
            token = tokens[position]
            # Short flags bundle: ``bash -lc '…'`` is as much a command
            # shell as ``bash -c '…'``, and matching only the exact ``-c``
            # missed it.
            if token.startswith('-') and not token.startswith('--') and 'c' in token[1:]:
                return _git_subcommand_head(' '.join(tokens[position + 1:]))
        return _git_subcommand_head(' '.join(tokens[index + 1:]))
    if program != 'git':
        return None
    index += 1
    while index < len(tokens) and tokens[index].startswith('-'):
        option = tokens[index].split('=', 1)[0]
        if option in _GIT_PRECOMMAND_VALUE_OPTIONS and '=' not in tokens[index]:
            index += 2
        else:
            index += 1
    if index >= len(tokens):
        return None
    return tokens[index], tokens[index + 1:]


def git_subcommand_of(segment: str) -> str:
    """``'restore'`` for ``git -C x restore a.js``; ``''`` for a non-git segment.

    Public because the remembered-permission signature needs the SAME notion
    of "which git" this module uses. Keying a remembered approval on the bare
    program meant one "always allow" on ``git status`` also granted every
    future ``git restore`` — the read-only grant an operator would give
    without a second thought, silently covering the destructive one.
    """
    head = _git_subcommand_head(segment)
    return head[0] if head else ''


def _git_revert_pathspecs(segment: str):
    """``(subcommand, pathspecs, opaque)`` for a git restore/checkout segment.

    ``None`` when the segment does not invoke ``git restore``/``git checkout``
    at all. ``opaque`` marks a pathspec set that cannot be determined
    statically, which the caller must treat as whole-tree.
    """
    head = _git_subcommand_head(segment)
    if head is None:
        return None
    subcommand, rest = head
    if subcommand not in ('restore', 'checkout'):
        return None

    pathspecs: list = []
    opaque = False
    after_separator = False
    skip_next = False
    for token in rest:
        if skip_next:
            skip_next = False
            continue
        if not after_separator:
            if token == '--':
                # Everything past ``--`` is a pathspec, flag-looking or not.
                # Parsing flags beyond it let ``git checkout -- -s .`` consume
                # the ``.`` as an option value and read as scoped.
                after_separator = True
                continue
            if token.split('=', 1)[0] == _GIT_PATHSPEC_FROM_FILE:
                opaque = True
                if '=' not in token:
                    skip_next = True
                continue
            if token in _GIT_REVERT_VALUE_OPTIONS:
                skip_next = True
                continue
            if token.startswith('-'):
                continue
        pathspecs.append(token)
    return subcommand, pathspecs, opaque


# ``git rm`` / ``git clean`` are the agent's to run — they are index and
# working-tree operations, not branch state. But both have a whole-tree
# form that erases work with no commit behind it:
#
#   git rm -r --cached .   unstages the entire index
#   git rm -rf .           deletes the entire worktree
#   git clean -fd          deletes every untracked file, including whatever
#                          the agent just wrote and has not had committed
#
# Same shape as the restore rule: split on PATHSPEC BREADTH, and treat a
# pathspec-less invocation as whole-tree. Denying the verbs outright to
# stop these is what left the agent unable to delete a single file with git.
_GIT_WORKTREE_WIPE_SUBCOMMANDS = frozenset({'rm', 'clean'})


# Git that belongs to the ORCHESTRATOR, blocked here as well as at the
# transport floor — because the floor alone does not hold.
#
# ``--disallowedTools`` matches the Bash command by PREFIX, so
# ``git commit`` is refused but ``sh -c 'git commit'``, ``/usr/bin/git
# commit`` and ``env git commit`` all sail past it: the string no longer
# starts with the denied prefix. That is not a hypothetical — it is three
# characters of shell. A guarantee that a wrapper defeats is not one.
#
# This rule is program-anchored instead of prefix-anchored: it resolves the
# real program behind env assignments, wrappers (``env``/``timeout``/…) and
# ``sh -c``/``eval`` payloads, then looks at the actual subcommand. Floor
# severity — the operator can loosen a lot of this module, but not who owns
# the branch, which is the same stance the transport floor takes.
_GIT_ORCHESTRATOR_OWNED = frozenset({
    # refs + commits
    'commit', 'merge', 'rebase', 'reset', 'checkout', 'switch', 'branch',
    'cherry-pick', 'revert', 'am', 'tag', 'bisect',
    # remotes
    'push', 'pull', 'fetch', 'clone', 'remote', 'send-pack', 'receive-pack',
    # history rewriting
    'filter-branch', 'filter-repo', 'fast-import', 'replace',
    # code execution + reach outside the clone
    'config', 'worktree', 'submodule', 'sparse-checkout',
    # plumbing that composes back into a commit or a ref
    'commit-tree', 'write-tree', 'read-tree', 'mktree', 'hash-object',
    'update-ref', 'update-index', 'symbolic-ref', 'checkout-index',
})


def _detect_git_orchestrator_owned(segments) -> list:
    for segment in segments:
        head = _git_subcommand_head(segment)
        if head is None:
            continue
        subcommand, _rest = head
        if subcommand in _GIT_ORCHESTRATOR_OWNED:
            return [_Candidate(
                RiskCategory.OUT_OF_SCOPE, True,
                f'git {subcommand} belongs to the orchestrator, which owns '
                'the branch state and publishing — ask it to do this instead '
                'of running it here',
                'git.orchestrator_owned',
            )]
        # A subcommand assembled at runtime (``git $CMD``) cannot be read
        # statically. Ambiguity favours ASK over ALLOW here, exactly as it
        # does everywhere else in this module.
        if '$' in subcommand or '`' in subcommand:
            return [_Candidate(
                RiskCategory.OUT_OF_SCOPE, False,
                'the git subcommand is built at runtime, so it cannot be told '
                'apart from one the orchestrator owns',
                'git.opaque_subcommand',
            )]
    return []


def _detect_git_worktree_wipe(segments) -> list:
    for segment in segments:
        head = _git_subcommand_head(segment)
        if head is None:
            continue
        subcommand, rest = head
        if subcommand not in _GIT_WORKTREE_WIPE_SUBCOMMANDS:
            continue
        pathspecs: list = []
        after_separator = False
        dry_run = False
        for token in rest:
            if not after_separator:
                if token == '--':
                    after_separator = True
                    continue
                if token.startswith('-'):
                    # A dry run deletes nothing. Prompting for it is noise,
                    # and noise is what teaches an operator to click through
                    # the prompt that matters. ``-n`` is dry-run for both
                    # verbs; short flags bundle, so scan the cluster.
                    if token == '--dry-run' or (
                        not token.startswith('--') and 'n' in token[1:]
                    ):
                        dry_run = True
                    continue
            pathspecs.append(token)
        if dry_run:
            continue
        # No pathspec at all is the whole tree for both verbs.
        if not pathspecs or not all(_is_narrow_pathspec(p) for p in pathspecs):
            return [_Candidate(
                RiskCategory.DESTRUCTIVE_FS, False,
                f'git {subcommand} across the whole working tree — this '
                'erases work that has no commit behind it',
                'fs.git_worktree_wipe',
            )]
    return []


def git_revert_breadth(segment: str) -> str:
    """``'whole-tree'`` / ``'scoped'`` / ``''`` (not a git working-tree revert).

    Public for the same reason as :func:`git_subcommand_of`: a remembered
    approval of a SCOPED revert must not silently cover the whole-tree form,
    which is the unrecoverable one.
    """
    parsed = _git_revert_pathspecs(segment)
    if parsed is None:
        return ''
    subcommand, pathspecs, opaque = parsed
    if not pathspecs and subcommand == 'checkout' and not opaque:
        # Pathspec-less ``git checkout`` is branch movement — owned by the
        # transport's --disallowedTools floor and the prompt, not here.
        return ''
    if opaque or not pathspecs or not all(_is_narrow_pathspec(p) for p in pathspecs):
        return 'whole-tree'
    return 'scoped'


# Destructive FORMS of git verbs that are otherwise fine.
#
# ``stash``/``apply``/``reflog`` are permitted at the transport floor
# because they are worktree operations, not branch-state ones. Each has one
# or two shapes that genuinely destroy something, and denying the whole
# verb to stop them cost far more than it bought — an operator could not
# ask the agent to set changes aside or find a lost commit at all.
#
#   stash drop / stash clear  — throws away a stash. The stash was the
#                               recovery path; dropping it removes the
#                               reason stash is safer than a bare restore.
#   reflog expire / delete    — destroys the very record used to find lost
#                               commits.
#   apply --unsafe-paths      — the one apply form that can write OUTSIDE
#                               the worktree.
_GIT_DESTRUCTIVE_FORMS = {
    'stash': ({'drop', 'clear'}, 'discards a stash — the saved work in it is gone'),
    'reflog': (
        {'expire', 'delete'},
        'destroys the reflog, which is what finding a lost commit depends on',
    ),
}
_GIT_APPLY_UNSAFE = '--unsafe-paths'


def _detect_git_destructive_form(segments) -> list:
    for segment in segments:
        head = _git_subcommand_head(segment)
        if head is None:
            continue
        subcommand, rest = head
        if subcommand == 'apply':
            if any(token.split('=', 1)[0] == _GIT_APPLY_UNSAFE for token in rest):
                return [_Candidate(
                    RiskCategory.OUT_OF_SCOPE, False,
                    'git apply --unsafe-paths can write outside the worktree',
                    'fs.git_apply_unsafe',
                )]
            continue
        forms = _GIT_DESTRUCTIVE_FORMS.get(subcommand)
        if not forms:
            continue
        verbs, reason = forms
        # The destructive verb is the first non-flag token after the
        # subcommand: ``git stash drop`` / ``git reflog expire --all``.
        for token in rest:
            if token.startswith('-'):
                continue
            if token in verbs:
                return [_Candidate(
                    RiskCategory.DESTRUCTIVE_FS, False, reason,
                    f'fs.git_{subcommand}_destructive',
                )]
            break
    return []


def _detect_git_whole_tree_revert(segments) -> list:
    for segment in segments:
        if git_revert_breadth(segment) == 'whole-tree':
            return [_Candidate(
                RiskCategory.DESTRUCTIVE_FS, False,
                'discards uncommitted work across the repository — the task '
                'output is not committed yet, so this is unrecoverable',
                'fs.git_revert_all',
            )]
    return []


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
            _CATASTROPHIC_RM_TARGET.match(_collapsed_slashes(t)) for t in targets
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

# Backtick/``$(...)`` command substitution wrapping a fetch — checked
# separately from ``eval_remote_fetch`` (which requires a literal
# ``eval``/``bash -c``/``sh -c`` prefix) because a BARE substitution used
# directly as (part of) the command is just as much an RCE, and because
# the backtick form must run on the RAW command: ``deobfuscate_command``
# DELETES backtick markers outright (rather than normalizing them), so
# `` `curl -s url` `` is invisible in the de-obfuscated text the other
# checks run on — the very thing that makes the shell RUN the fetched
# output as a command, not just print it, disappears with the backticks.
_BACKTICK_SUBSTITUTION_FETCH = re.compile(r'`[^`]*\b(?:curl|wget)\b[^`]*`')
_DOLLAR_PAREN_SUBSTITUTION_FETCH = re.compile(r'\$\([^()]*\b(?:curl|wget)\b[^()]*\)')

# A script saved to a file (``curl ... -o <path>`` / ``wget ... -O <path>``)
# and then invoked directly is remote-exec even with NO pipe into a shell
# anywhere in the command — only whether some LATER segment references
# the exact path the fetch just wrote.
_DOWNLOAD_OUTPUT_FLAG = re.compile(
    r'\b(?:curl|wget)\b[^|;&\n]*?(?:-[oO]\s+|--output(?:-document)?[= ])(\S+)'
)
_FETCH_PROGRAMS = frozenset({'curl', 'wget'})


def _detect_download_then_exec(segments: list) -> list:
    downloaded: set = set()
    download_segment_indices: set = set()
    for index, segment in enumerate(segments):
        if segment_program(segment) not in _FETCH_PROGRAMS:
            continue
        match = _DOWNLOAD_OUTPUT_FLAG.search(segment)
        if match:
            downloaded.add(match.group(1).strip('\'"'))
            download_segment_indices.add(index)
    if not downloaded:
        return []
    for index, segment in enumerate(segments):
        if index in download_segment_indices:
            continue
        tokens = [t.strip('\'"') for t in segment.strip().split() if t]
        if any(token in downloaded for token in tokens):
            return [_Candidate(
                RiskCategory.REMOTE_EXEC, True,
                'executes a file just downloaded from the network',
                'rce.download_then_exec',
            )]
    return []


def _detect_remote_exec(command: str, deob: str, segments: list) -> list:
    for name, regex in _PHISHING_PATTERNS:
        if name in _REMOTE_EXEC_PHISHING and regex.search(deob):
            return [_Candidate(
                RiskCategory.REMOTE_EXEC, True,
                'pipes a downloaded script straight into a shell', f'rce.{name}',
            )]
    if _SOURCE_PROCESS_SUB.search(deob):
        return [_Candidate(
            RiskCategory.REMOTE_EXEC, True,
            'sources a remote script via process substitution', 'rce.source_proc_sub',
        )]
    if _BACKTICK_SUBSTITUTION_FETCH.search(command) \
            or _DOLLAR_PAREN_SUBSTITUTION_FETCH.search(deob):
        return [_Candidate(
            RiskCategory.REMOTE_EXEC, True,
            'command-substitutes a network fetch straight into the shell',
            'rce.command_substitution_fetch',
        )]
    return _detect_download_then_exec(segments)


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

# The orchestrator's own git operations always push to the ONE
# pre-configured task remote — adding a foreign remote (or pushing
# straight to an inline URL) and pushing the repo to it has no
# legitimate task-agent use, so this is floor-severity like the
# reverse-shell patterns above. For Claude this is separately (and
# redundantly) caught by a CLI-level ``--disallowedTools`` floor, but
# Codex has no such transport-level equivalent — this classifier is the
# ONLY line of defense for that transport, so it must not be empty.
_GIT_REMOTE_ADD = re.compile(r'\bgit\s+remote\s+add\b')
_GIT_PUSH = re.compile(r'\bgit\s+push\b')
_GIT_PUSH_TO_URL = re.compile(
    r'\bgit\s+push\b[^|;&\n]*\b(?:https?://|git@|ssh://)',
)

# A DNS lookup whose query embeds command-substituted data (``$(...)`` or
# a backtick form) is a DNS-exfiltration channel: no legitimate lookup
# needs its query built from shell substitution. Checked against the RAW
# command for the backtick form for the same reason as the remote-exec
# backtick check above — ``deobfuscate_command`` deletes backtick markers.
_DNS_EXFIL_DOLLAR_PAREN = re.compile(r'\b(?:dig|nslookup|host)\b[^|;&\n]*\$\(')
_DNS_EXFIL_BACKTICK = re.compile(r'\b(?:dig|nslookup|host)\b[^|;&\n`]*`')


def _detect_network_exfil(deob: str, command: str = '') -> list:
    """``deob`` is the de-obfuscated command every existing pattern here
    runs against (unchanged). ``command`` is the RAW pre-deobfuscation
    text, needed ONLY for the DNS-exfil backtick form — ``deobfuscate_command``
    deletes backtick markers outright, so that form is invisible in ``deob``.
    Defaults to ``deob`` when the caller doesn't have a raw copy handy."""
    command = command or deob
    if _REVERSE_SHELL_DEV.search(deob) or _NC_EXEC.search(deob) \
            or _SOCAT_EXEC.search(deob):
        return [_Candidate(
            RiskCategory.NETWORK_EXFIL, True,
            'opens a reverse shell / raw network connection', 'net.reverse_shell',
        )]
    if (_GIT_REMOTE_ADD.search(deob) and _GIT_PUSH.search(deob)) \
            or _GIT_PUSH_TO_URL.search(deob):
        return [_Candidate(
            RiskCategory.NETWORK_EXFIL, True,
            'pushes the repository to a remote outside the task config',
            'net.git_exfil',
        )]
    if _DNS_EXFIL_DOLLAR_PAREN.search(deob) or _DNS_EXFIL_BACKTICK.search(command):
        return [_Candidate(
            RiskCategory.NETWORK_EXFIL, True,
            'DNS query embeds command-substituted data — exfiltration channel',
            'net.dns_exfil',
        )]
    if _CURL_UPLOAD.search(deob) or _WGET_POST.search(deob):
        return [_Candidate(
            RiskCategory.NETWORK_EXFIL, False,
            'uploads a local file to a remote host', 'net.http_upload',
        )]
    if _SCP_SFTP.search(deob) or _RSYNC_REMOTE.search(deob) \
            or _NC_CONNECT.search(deob):
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


# Programs that read/transfer file CONTENT — a bare filename with no
# credential-looking name (``cat my_custom_deploy_key``) is otherwise
# invisible to the path-pattern checks above, so when the shell's cwd is
# ALREADY inside a known credential directory (e.g. a prior `cd ~/.ssh`
# in the same persistent shell — Claude's Bash tool keeps ONE shell
# across calls), any of these programs running there is itself the
# credential read, regardless of what it names.
_FILE_READ_PROGRAMS = frozenset({
    'cat', 'less', 'more', 'head', 'tail', 'cp', 'scp', 'rsync', 'base64',
    'xxd', 'od', 'strings', 'vim', 'vi', 'nano', 'emacs', 'grep', 'sed', 'awk',
})


def _reads_a_file(text: str) -> bool:
    return any(
        segment_program(segment) in _FILE_READ_PROGRAMS
        for segment in split_command_segments(text)
    )


def _detect_credential_read(text: str, cwd: str = '') -> list:
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
    if cwd:
        for rule_id, regex in _CRED_PATHS:
            if regex.search(cwd) and _reads_a_file(text):
                return [_Candidate(
                    RiskCategory.CREDENTIAL_READ, False,
                    'reads a file while the shell is inside a credential directory',
                    f'{rule_id}.cwd',
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
    # Local agent/task orchestration — the same bounded-local class as
    # ``agent``/``task``: ``workflow`` fans out subagents on the host,
    # ``monitor`` waits on a local condition, ``taskoutput``/``taskstop``
    # read/stop a background task. None reach off-machine, so they must not
    # trip the red "external capability" gate (and a background ``workflow``
    # must run without re-prompting every time it notifies back).
    'workflow', 'monitor', 'taskoutput', 'taskstop',
    # The agent asking the OPERATOR a question (not a network/external action).
    # the host renders it as an answer UI; it must not look like a scary new
    # "external capability".
    'askuserquestion',
    # Presenting a plan / leaving plan mode. A bounded-local capability: the
    # agent proposes a plan for review and never reaches off-machine. The host
    # captures the plan and renders it; it must not trip the red "external
    # capability" gate (it would otherwise block the plan-mode handoff).
    'exitplanmode',
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
        f'{name} is a new capability the host does not recognize — approve it '
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
    candidates += _detect_network_exfil(deob, command)
    candidates += _detect_remote_exec(command, deob, segments)
    candidates += _detect_escape(command)
    candidates += _detect_destructive_fs(deob, segments)
    candidates += _detect_git_whole_tree_revert(segments)
    candidates += _detect_git_destructive_form(segments)
    candidates += _detect_git_worktree_wipe(segments)
    candidates += _detect_git_orchestrator_owned(segments)
    candidates += _detect_credential_read(deob, cwd)
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
        # NOT cwd-aware here (unlike the Bash-command path below): a
        # bare relative ``file_path`` has no shell "program" to check
        # via ``_reads_a_file``, so the cwd-fallback in
        # ``_detect_credential_read`` would never actually fire for a
        # tool-input path — passing ``cwd`` through would be dead code.
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
    workspace out-of-scope classifiers from ``sandbox_scope``,
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

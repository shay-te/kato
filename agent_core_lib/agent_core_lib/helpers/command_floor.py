"""The floor: what an agent may never run, whatever the CLI or the mode.

Two layers enforce this and they are not interchangeable:

* **Layer A — the CLI's own denylist.** A CLI that accepts a tool-deny flag
  refuses these before the agent's request reaches anything else, including in
  bypass modes where no per-tool prompt fires. Strong, but only available on a
  CLI that has such a flag.
* **Prompt rules.** For a CLI with no deny flag, the prompt is the only layer
  that says "never run this". Weaker — a model can disregard an instruction in
  a way it cannot disregard an unavailable tool — which is exactly why the
  wording must not be a separate, hand-maintained list.

Both layers render from the tuples below. That is the point of this module: a
subcommand added here reaches every transport at once. When the two were
maintained separately, one transport's list said "git … anything" in prose
while the other enforced 40 named subcommands, and nothing could tell you
whether they agreed.

**The line these lists draw**, stated once so the next edit can check itself
against it rather than guessing:

    The orchestrator owns REFS, COMMITS, REMOTES, HISTORY and CONFIG.
    The agent owns the INDEX and the WORKING TREE.

So anything that moves HEAD or a branch, creates a commit, talks to a remote,
rewrites history, or sets config (the hook/RCE surface) is denied — including
the plumbing that reaches the same capability under another name. Everything
else is the agent's: ``add``, ``rm``, ``mv``, ``clean``, ``restore``,
``stash``, ``apply``, ``reflog``, and every read-only command.
"""

from __future__ import annotations

# Mutating git, denied because the orchestrator owns the branch state machine
# and the publish path: an agent commit or push would race it and could
# publish unvalidated work. READ-ONLY git (status/log/diff/show/blame) is
# absent on purpose — the self-review workflow needs ``git diff master...``,
# and denying it had the agent reporting "git is forbidden" for work it was
# asked to do.
#
# ``restore``, ``stash``, ``apply`` and ``reflog`` are DELIBERATELY ABSENT:
# they are file/worktree operations, not branch-state ones. Their destructive
# FORMS (``stash drop``, ``apply --unsafe-paths``, ``reflog expire``,
# ``restore .``) are caught by argv in the content-aware guard instead of by
# denying the whole verb.
GIT_MUTATING_SUBCOMMANDS: tuple[str, ...] = (
    'push', 'commit', 'merge', 'rebase', 'reset', 'checkout', 'switch',
    'cherry-pick', 'revert', 'am',
    'tag', 'branch', 'remote', 'fetch', 'pull', 'clone',
    'init', 'config', 'gc', 'prune', 'filter-branch', 'filter-repo',
    'update-ref', 'update-index', 'symbolic-ref', 'worktree', 'submodule',
    'sparse-checkout', 'bisect', 'notes', 'replace', 'fast-import',
    # PLUMBING. Listing only the porcelain left every capability reachable one
    # layer down: ``hash-object -w`` + ``mktree`` + ``commit-tree`` builds a
    # commit and ``send-pack`` publishes it — the exact race the porcelain
    # entries prevent, with none of them invoked. ``checkout-index -a -f`` is
    # additionally a whole-tree working-copy overwrite.
    'read-tree', 'write-tree', 'commit-tree', 'hash-object', 'mktree',
    'checkout-index', 'send-pack', 'receive-pack', 'index-pack',
    'unpack-objects', 'pack-refs', 'bundle', 'prune-packed',
)

# Programs with NO legitimate use in a task workspace. Deliberately TINY and
# program-token-anchored, because a CLI deny flag matches by program prefix
# rather than by full argument string: dual-use programs (``rm``, ``chmod``,
# ``dd``) and pipelines (``curl … | sh``) CANNOT go here without over-blocking
# real work, and are handled precisely by the content-aware guard instead.
FLOOR_DENY_PROGRAMS: tuple[str, ...] = (
    # Filesystem formatters / swap — never legitimate on a workspace clone.
    'mkfs', 'mkfs.ext2', 'mkfs.ext3', 'mkfs.ext4', 'mkfs.xfs',
    'mkfs.btrfs', 'mkfs.vfat', 'mkfs.fat', 'mkfs.ntfs', 'mkswap',
    # Sandbox / namespace escape primitives.
    'nsenter', 'unshare', 'chroot',
    # Host power control — an agent fixing code never reboots the machine.
    'shutdown', 'reboot', 'halt', 'poweroff',
)

# Denied ONLY where no per-tool prompt fires, so the content-aware guard never
# runs: actions whose safety depends on an operator seeing them first.
# Permitted in every attended mode, withdrawn when nobody is watching.
UNSUPERVISED_DENY_SUBCOMMANDS: tuple[str, ...] = ('restore',)

# --- git access levels -------------------------------------------------------
#
# STRICT (the default) is the line drawn in the module docstring: the agent
# owns the index and the working tree, the orchestrator owns everything else.
#
# LOCAL hands the agent the HISTORY OF THE BRANCH IT IS ON as well — commit,
# amend, squash, rebase, reset, merge, cherry-pick, revert, tag — because a
# developer driving a chat wants to say "commit this" or "squash those two"
# and have it done, not refused. Three things stay the orchestrator's in
# LOCAL too, and each has its own reason:
#
# * anything that talks to a remote (push / pull / fetch / clone / remote,
#   send-pack …): publishing stays an operator action, the push carries the
#   orchestrator's token to a remote it verified, and a fetch/push to an
#   arbitrary URL is an exfiltration channel;
# * leaving the branch (checkout / switch / branch / bisect, symbolic-ref):
#   the orchestrator publishes, diffs and finds pull requests by the task's
#   branch, so work committed on another branch is work it cannot see;
# * config, worktree, submodule and the ref/object plumbing: config is the
#   hook / code-execution surface, and the plumbing can move refs the
#   orchestrator's commit counts read (``update-ref refs/remotes/...``).
#
# Unknown or empty → STRICT. A typo must never widen what an agent may run.
GIT_ACCESS_STRICT = 'strict'
GIT_ACCESS_LOCAL = 'local'
GIT_ACCESS_LEVELS: tuple[str, ...] = (GIT_ACCESS_STRICT, GIT_ACCESS_LOCAL)

#: Released to the agent in LOCAL access — and only these. Every entry must
#: also be in ``GIT_MUTATING_SUBCOMMANDS`` (pinned by a test): LOCAL only ever
#: subtracts from the strict list, it never invents a separate one.
GIT_LOCAL_HISTORY_SUBCOMMANDS: tuple[str, ...] = (
    'commit', 'merge', 'rebase', 'reset', 'cherry-pick', 'revert', 'am', 'tag',
)


def normalized_git_access(value: object) -> str:
    """``'local'`` only when asked for exactly; anything else is ``'strict'``."""
    text = str(value or '').strip().lower()
    return text if text in GIT_ACCESS_LEVELS else GIT_ACCESS_STRICT


def effective_git_access(value: object, *, unsupervised: bool) -> str:
    """The access a session actually gets.

    LOCAL is withdrawn where nobody is watching, for the same reason
    ``UNSUPERVISED_DENY_SUBCOMMANDS`` withdraws ``git restore`` there: a hard
    reset or a rebase is at least as destructive as a whole-tree restore, and
    in a mode with no per-tool prompt nobody would see it before it ran.
    """
    if unsupervised:
        return GIT_ACCESS_STRICT
    return normalized_git_access(value)


def git_denied_subcommands(git_access: object = GIT_ACCESS_STRICT) -> tuple[str, ...]:
    """The git subcommands refused to the agent at ``git_access``.

    The ONE list both enforcement layers read — the CLI deny flag and the
    content-aware guard — so the two can never disagree about a level.
    """
    if normalized_git_access(git_access) == GIT_ACCESS_LOCAL:
        released = set(GIT_LOCAL_HISTORY_SUBCOMMANDS)
        return tuple(sub for sub in GIT_MUTATING_SUBCOMMANDS if sub not in released)
    return GIT_MUTATING_SUBCOMMANDS


def git_access_prompt_text(git_access: object) -> str:
    """What a LOCAL session is told about git. ``''`` at STRICT.

    STRICT needs nothing here: the orchestrator's own guidance already states
    that line. LOCAL has to say so explicitly AND say that it overrides that
    guidance, or the agent keeps routing a commit through the orchestrator (or
    refusing it) because an earlier message told it commits are not its own.
    Rendered from the same tuples the deny list uses.
    """
    if normalized_git_access(git_access) != GIT_ACCESS_LOCAL:
        return ''
    released = ', '.join(f'`git {sub}`' for sub in GIT_LOCAL_HISTORY_SUBCOMMANDS)
    return (
        'GIT ACCESS FOR THIS SESSION: LOCAL. This overrides any other '
        'instruction that says committing is not yours or must be requested.\n'
        f'- You may run {released} yourself, on the branch that is checked '
        'out, when the operator asks or the task clearly needs it '
        '(e.g. "commit this", "squash these", "rebase onto master"). Write '
        'commit messages the way the repository\'s own conventions say.\n'
        '- Stay on the checked-out branch: no `git checkout` / `git switch` / '
        '`git branch` / `git bisect`. Work committed on another branch is '
        'work the orchestrator cannot publish.\n'
        '- Nothing that talks to a remote: no `git push`, `git pull`, '
        '`git fetch`, `git clone`, `git remote`. The orchestrator pushes '
        'when the operator asks for it. No `git config`.\n'
        '- Do not rewrite commits that are already pushed (amend, rebase or '
        'reset past them) unless the operator asks for exactly that: the '
        'orchestrator will refuse to push a rewritten branch.\n'
        '- `git reset --hard` discards every uncommitted change; say what it '
        'will throw away before you run it.\n'
    )


def git_access_change_text(git_access: object, previous: object = None) -> str:
    """What to tell an agent whose git access is ``git_access`` now, when it was
    last told ``previous`` (``None`` = never told). ``''`` when nothing changed.

    For a transport with no system prompt (the rules ride in the conversation),
    so it is told once per change rather than on every message. Back to strict
    after local needs saying out loud: the earlier "you may commit" is still in
    the conversation, and silence would leave it standing.
    """
    access = normalized_git_access(git_access)
    if access == GIT_ACCESS_LOCAL:
        return '' if previous is not None and normalized_git_access(previous) == GIT_ACCESS_LOCAL \
            else git_access_prompt_text(GIT_ACCESS_LOCAL)
    if previous is not None and normalized_git_access(previous) == GIT_ACCESS_LOCAL:
        released = ', '.join(f'`git {sub}`' for sub in GIT_LOCAL_HISTORY_SUBCOMMANDS)
        return (
            'GIT ACCESS FOR THIS SESSION: STRICT again. This replaces the earlier '
            f'git access message: do not run {released} any more. The '
            'orchestrator makes the commits again.\n'
        )
    return ''


def cli_deny_patterns(tokens: tuple[str, ...], *, program: str = '') -> tuple[str, ...]:
    """Render ``tokens`` as CLI tool-deny patterns, in both accepted forms.

    ``program`` prefixes each token (``'git'`` turns ``push`` into
    ``git push``); omit it for bare program names. Both the colon form
    (``Bash(git push:*)``) and the space form (``Bash(git push *)``) are
    emitted because different CLI versions accept different ones — a floor
    that silently matches nothing is worse than no floor.
    """
    prefix = f'{program} ' if program else ''
    return tuple(
        pattern
        for token in tokens
        for pattern in (f'Bash({prefix}{token}:*)', f'Bash({prefix}{token} *)')
    )


def prompt_floor_rules() -> str:
    """The floor as prompt text, for a CLI with no tool-deny flag.

    Rendered from the same tuples the CLI denylist uses, so a transport that
    can only enforce this in the prompt still names exactly what the enforcing
    transports block. Grouped rather than exhaustive per-line: a wall of forty
    subcommands reads as noise, and the categories are what the model needs.

    States what IS allowed, not only what is not. A list of prohibitions with
    no counterpart teaches the model that git is dangerous ground in general,
    and it then over-refuses the working-tree operations that are deliberately
    permitted — an operator asking "undo your change to that file" had to say
    it three times before the agent would run ``git restore``. The permission
    has to be as explicit as the denial or it is not read as one.
    """
    return (
        'Hard limits — these are refused at the tool level for other agents '
        'and are equally forbidden for you:\n'
        '- No git that changes refs, commits, remotes, history or config: '
        + ', '.join(GIT_MUTATING_SUBCOMMANDS[:12]) + ', and their plumbing '
        'equivalents. Read-only git (status, log, diff, show, blame) is fine.\n'
        '- Working-tree git IS yours, needs no approval, and is not covered '
        'by the rule above: `git restore <path>` / `git restore --staged '
        '<path>`, `git stash`, `git apply`. Undoing an edit you made to a '
        'file is ordinary work — when asked to revert or drop a change, do '
        'it with `git restore <path>` and do not report yourself blocked. '
        'Scope it to the paths concerned rather than the whole tree.\n'
        '- No filesystem formatting or swap setup: '
        + ', '.join(p for p in FLOOR_DENY_PROGRAMS if p.startswith(('mkfs', 'mkswap')))
        + '.\n'
        '- No sandbox or namespace escape: '
        + ', '.join(('nsenter', 'unshare', 'chroot')) + '.\n'
        '- No host power control: '
        + ', '.join(('shutdown', 'reboot', 'halt', 'poweroff')) + '.\n'
    )

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
    """
    return (
        'Hard limits — these are refused at the tool level for other agents '
        'and are equally forbidden for you:\n'
        '- No git that changes refs, commits, remotes, history or config: '
        + ', '.join(GIT_MUTATING_SUBCOMMANDS[:12]) + ', and their plumbing '
        'equivalents. Read-only git (status, log, diff, show, blame) is fine.\n'
        '- No filesystem formatting or swap setup: '
        + ', '.join(p for p in FLOOR_DENY_PROGRAMS if p.startswith(('mkfs', 'mkswap')))
        + '.\n'
        '- No sandbox or namespace escape: '
        + ', '.join(('nsenter', 'unshare', 'chroot')) + '.\n'
        '- No host power control: '
        + ', '.join(('shutdown', 'reboot', 'halt', 'poweroff')) + '.\n'
    )

"""Pure, product-agnostic introspection of a shell command string.

Several guards need to look *inside* a Bash command before it runs —
the workspace sandbox classifier (``claude_core_lib.sandbox_scope``), the
Action Guard risk engine (``command_policy``), and the agent transports.
They all need the same primitives:

* **de-obfuscation** — strip shell quoting / backslash escaping / ``$HOME``
  indirection so a buried token can't hide from a scanner;
* **segmentation** — split a chained command on ``&&`` / ``||`` / ``;`` /
  ``|`` so ``safe && dangerous`` is judged segment-by-segment;
* **program extraction** — find the program a segment actually invokes,
  stepping over leading ``VAR=val`` env assignments and benign wrapper
  programs (``env``/``xargs``/``time``/``timeout``…);
* **escape classification** — flag commands that reach the host *around*
  any path sandbox (container runtimes, privilege/namespace tools).

These are deliberately filesystem-free and deterministic. They defeat
*static* obfuscation only — a token built at RUNTIME from an arbitrary
``$VAR``, base64, or fetched data is invisible here; OS-level confinement
(the host Docker sandbox) is the structural backstop for those.

Lives in ``agent_core_lib`` (the shared agent-behavior base) so the
Claude sandbox classifier and the risk engine reuse one implementation
instead of duplicating the de-obfuscator.
"""

from __future__ import annotations

import re

# Shell ``$HOME`` / ``${HOME}`` → ``~`` so home-relative tokens resolve.
_HOME_VAR = re.compile(r'\$\{?HOME\}?')


def deobfuscate_command(command: str) -> str:
    """Strip shell quoting/escaping so a token can't hide from a scanner.

    ``/Use"rs"/dev`` (quote-split), ``cat\\ /Users/x`` (backslash-escaped), and
    ``'...'``/``\"...\"``/backtick wrappers around a buried token are all
    flattened so the raw text is visible. ``$HOME``/``${HOME}`` collapse to
    ``~``.

    NOTE: this defeats *static* obfuscation only — a token built at RUNTIME
    from an arbitrary ``$VAR``, base64, or fetched data cannot be seen here;
    that is what OS-level confinement (the host Docker sandbox) is for.
    """
    text = str(command or '')
    text = text.replace('\\', '')
    text = text.replace('"', '').replace("'", '').replace('`', '')
    return _HOME_VAR.sub('~', text)


# Split a chained command into its independently-executed segments so each
# is judged on its own (``cd /x && docker run`` → two segments).
_SEGMENT_SPLIT = re.compile(r'&&|\|\||[;|]')


def split_command_segments(command: str) -> list[str]:
    """Return the ``&&`` / ``||`` / ``;`` / ``|`` separated segments of a
    command (already de-obfuscated by the caller if desired)."""
    return _SEGMENT_SPLIT.split(str(command or ''))


# ASCII-only on purpose: a shell env-var name is POSIX ``[A-Za-z_][A-Za-z0-9_]*``
# (bash rejects non-ASCII identifiers), and the permission-signature mirror in
# ``permissionEnvelope.js`` matches with the ASCII class ``[A-Za-z_][A-Za-z0-9_]*``
# too — keeping this ASCII keeps the two in lockstep (a Unicode ``\w`` here made
# the Python and JS signatures diverge on a non-ASCII assignment token).
_ENV_ASSIGNMENT = re.compile(r'^[A-Za-z_]\w*=', re.ASCII)
# Benign wrapper programs that RUN another program (their last/inner argument);
# we step transparently through them so ``env docker``, ``xargs docker``,
# ``time docker``, ``timeout 10 docker`` don't hide a program behind the
# wrapper. NOT escape programs themselves — sudo/doas ARE escapes in their own
# right, so they are not listed here and get flagged directly.
_WRAPPER_PROGRAMS = frozenset({
    'env', 'xargs', 'command', 'nohup', 'time', 'nice', 'timeout',
    'stdbuf', 'setsid', 'ionice',
})


def program_token_index(tokens: list[str], start: int = 0) -> int:
    """Index of the token naming the program a command segment actually
    invokes, starting the scan at ``start`` — stepping over leading
    ``VAR=val`` env assignments AND benign wrapper programs
    (``env``/``xargs``/``time``/``nice``/``timeout``…) plus their own
    flags / numeric args, so ``env docker`` and ``timeout 10 docker``
    both resolve to ``docker``'s token. Returns ``len(tokens)`` when the
    scan runs off the end (only wrappers, e.g. a bare ``env``).

    Shared by ``segment_program`` (the Action Guard's program classifier)
    and the remembered-permission signature builder so the two agree on
    exactly what "the program" is behind a wrapper."""
    index = max(0, int(start or 0))
    while index < len(tokens):
        if _ENV_ASSIGNMENT.match(tokens[index]):
            index += 1
            continue
        program = tokens[index].rsplit('/', 1)[-1]
        if program not in _WRAPPER_PROGRAMS:
            return index
        # Step over the wrapper and any of ITS option flags / numeric args
        # (``nice -n 5 docker``, ``timeout 10 docker``) to reach the inner cmd.
        index += 1
        while index < len(tokens) and (
            tokens[index].startswith('-')
            or tokens[index].isdigit()
            or _ENV_ASSIGNMENT.match(tokens[index])
        ):
            index += 1
    return len(tokens)


def segment_program(segment: str) -> str:
    """The basename of the program a command segment actually invokes — stepping
    over leading ``VAR=val`` env assignments AND benign wrapper programs
    (``env``/``xargs``/``time``/``nice``/``timeout``…) plus their own flags/
    numeric args, so ``env docker`` resolves to ``docker``. '' when none."""
    tokens = [t for t in str(segment or '').strip().split() if t]
    index = program_token_index(tokens)
    if index >= len(tokens):
        return ''
    return tokens[index].rsplit('/', 1)[-1]


# Commands that operate OUTSIDE the task sandbox by nature, regardless of which
# paths they name: container runtimes can bind-mount any host path into a
# container the host never sees (``docker run -v /:/host``), and privilege /
# namespace tools step around the workspace entirely.
_ESCAPE_PROGRAMS = frozenset({
    'docker', 'docker-compose', 'podman', 'nerdctl', 'kubectl', 'ctr',
    'sudo', 'doas', 'chroot', 'nsenter', 'unshare',
})


def classify_command_escape(command: str) -> tuple[bool, str]:
    """Return ``(escapes, program)`` when a command invokes a container-runtime
    / privilege / namespace primitive (``docker``, ``sudo``, ``chroot``, …).

    These reach the host *around* any path sandbox. Checks the effective
    program of every ``&&``/``;``/``|`` segment (so ``cd /x && docker run``,
    ``sudo docker …`` and ``env/xargs/time docker`` are all caught),
    de-obfuscated first so quotes/$HOME can't hide the program name.
    """
    text = deobfuscate_command(command)
    for segment in split_command_segments(text):
        program = segment_program(segment)
        if program and program in _ESCAPE_PROGRAMS:
            return True, program
    return False, ''

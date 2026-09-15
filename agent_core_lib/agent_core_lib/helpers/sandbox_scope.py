"""Classify whether an agent tool call reaches outside its sandbox.

An agent session is spawned with a containment sandbox: the per-task
workspace clone (the ``cwd``) plus an explicit extra-directory set. When the agent asks permission to touch a filesystem path that
escapes ALL of those roots, the planning UI must (a) shout a warning
and (b) refuse to offer a *remembered* approval — an "allow always"
for an out-of-sandbox path would silently hand the agent standing
access outside the task folder on every future run. This module is the
single, pure place that decides "is this path inside the sandbox?" so
the streaming layer can annotate the permission event and every UI
surface agrees.

Pure + filesystem-free: paths are normalized lexically (``normpath``),
never ``realpath``'d, so the classification is deterministic, needs no
disk, and still catches ``../`` escapes (the actual attack vector).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

from agent_core_lib.agent_core_lib.helpers.command_introspection import (
    deobfuscate_command,
    program_token_index,
    split_heredoc_bodies,
)

# Tool-input keys that name a filesystem path the agent intends to
# touch. Covers Read/Edit/Write/MultiEdit (``file_path``), generic
# ``path``, and NotebookEdit (``notebook_path``). A bare Bash
# ``command`` is intentionally NOT inspected: a shell string can embed
# arbitrarily many paths and parsing it reliably is a fool's errand, so
# flagging it would either miss escapes (false safety) or nuke the
# usefulness of a remembered ``git``/``ls`` approval (false alarm).
# Path-argument tools are the ones where a remembered out-of-sandbox
# grant is actually dangerous, and they are classified exactly.
_PATH_KEYS = ('file_path', 'notebook_path', 'path', 'file')


def _candidate_paths(tool_input: Any) -> list[str]:
    if not isinstance(tool_input, dict):
        return []
    paths: list[str] = []
    for key in _PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            paths.append(value.strip())
    return paths


def _normalize(path: str, base: str) -> str:
    """Lexically resolve ``path`` (relative → against ``base``)."""
    if not os.path.isabs(path):
        path = os.path.join(base or '', path)
    return os.path.normpath(path)


def _is_within(path: str, root: str) -> bool:
    if not root:
        return False
    root_norm = os.path.normpath(root)
    return path == root_norm or path.startswith(root_norm + os.sep)


def _effective_roots(cwd: str, additional_dirs) -> list[str]:
    """Sandbox roots, widened to the whole task folder.

    the orchestrator clones every repo of a task as a SIBLING under one task
    workspace dir (``<workspaces>/<task_id>/<repo>``); the live session's
    ``cwd`` is one such repo. The "task folder" the operator means is its
    PARENT. We add that parent as a root so a file in ANY repo of the
    task reads as inside — even a sibling repo that never made it into
    the session's ``--add-dir`` set, which happens for real:
      * the sandbox is immutable post-spawn, so a repo cloned/synced
        after the session started is not in it; and
      * a comment-/review-driven respawn spawns with ``cwd`` only and NO
        ``--add-dir``s at all, leaving a single-root session.
    Both produced the UNA-2727 "inside-the-task file flagged as outside"
    false positive.

    Guards against ballooning the sandbox to unrelated trees:
      * the parent must be at least two levels deep (``/a/b``) — never
        '/' or '/Users' — so it can't swallow every other task's folder
        or the whole disk; and
      * a DIFFERENT task's folder (``<workspaces>/<other-task>``) is
        still outside, because the parent we add is THIS task's folder.
    """
    roots = [os.path.normpath(r) for r in (cwd, *tuple(additional_dirs)) if r]
    cwd_norm = os.path.normpath(cwd) if cwd else ''
    if cwd_norm and os.path.isabs(cwd_norm):
        task_folder = os.path.dirname(cwd_norm)
        deep_enough = task_folder.strip(os.sep).count(os.sep) >= 1
        if deep_enough and task_folder not in roots:
            roots.append(task_folder)
    return roots


def effective_sandbox_roots(
    cwd: str, additional_dirs: tuple[str, ...] | list[str] = (),
) -> list[str]:
    """The sandbox roots that count as "inside the task folder": the ``cwd``,
    the spawn-time ``--add-dir`` set, and the task-folder parent (see
    ``_effective_roots``). Public so the PREVENTIVE write-scope settings and
    this POST-HOC classifier share ONE boundary — a write allowed here is
    exactly a write that won't be warned about later. Empty when ``cwd`` is
    unknown."""
    return _effective_roots(cwd, additional_dirs)


def classify_tool_input_sandbox(
    tool_input: Any,
    cwd: str,
    additional_dirs: tuple[str, ...] | list[str] = (),
    allowed_paths: tuple[str, ...] | list[str] = (),
) -> tuple[bool, str]:
    """Return ``(outside, offending_path)`` for an agent tool input.

    ``outside`` is ``True`` when a filesystem-path argument resolves
    OUTSIDE every sandbox root — the task ``cwd`` plus the spawn-time
    ``--add-dir`` set. Relative paths resolve against ``cwd``. The first
    escaping path found is returned (in ``_PATH_KEYS`` order) so the UI
    can name it.

    ``allowed_paths`` are SPECIFIC files the product intentionally lets the
    agent touch even though they live outside the task folder — e.g. the orchestrator's
    configured ``lessons_path`` / ``architecture_doc_path``. The agent is
    SUPPOSED to read/write those, so an exact match is never flagged. They
    are passed in (not hard-coded) to keep this lib product-agnostic.

    Conservative on BOTH ends so it neither over-warns nor under-warns:
    - No path argument (e.g. a bare Bash command) → ``(False, '')``. The
      function only flags a path it can actually see escape, preserving
      remembered Bash/git approvals.
    - No sandbox roots known (unconfigured) → ``(False, '')`` rather
      than flag everything.
    """
    norm_roots = _effective_roots(cwd, additional_dirs)
    if not norm_roots:
        return False, ''
    norm_allowed = {_normalize(p, cwd) for p in allowed_paths if p}
    for raw_path in _candidate_paths(tool_input):
        resolved = _normalize(raw_path, cwd)
        if resolved in norm_allowed:
            continue
        if not any(_is_within(resolved, root) for root in norm_roots):
            return True, raw_path
    return False, ''


# ---------------------------------------------------------------------------
# Shell commands
#
# Every false alarm this used to raise came from reading the command as one
# flat string of regex matches instead of the way a shell runs it: a ``cd`` on
# its own line was never followed, a ``(cd x && …)`` subshell leaked its
# directory into the rest of the command, ``$R/helper_scripts`` was read as a
# relative ``R/helper_scripts``, ``http://localhost:8983/solr`` produced a
# path ``8983/solr``, a match started half-way through a word
# (``Table/Users/CustomField.js`` → ``/Users/CustomField.js``), and a relative
# symlink target was resolved from the wrong directory. Each of those stopped
# a legitimate in-task command for an approval. So the command is now walked
# statement by statement, tracking the directory, variables and subshells.
# ---------------------------------------------------------------------------

# Only USER / PROJECT space is interesting: other repos and secrets live under
# the home tree. System paths (/usr,/etc,/tmp…), URLs (//host/…), and
# glob/regex fragments (``/main/*``) fall outside these prefixes and are
# ignored — that is what keeps command scanning from drowning the operator in
# false alarms. (A relative path that climbs with ``..`` is flagged wherever it
# lands: climbing out is itself the signal.)
_USER_SPACE_PREFIXES = ('/Users/', '/home/')

# Stand-ins that survive ``deobfuscate_command`` (no quotes, no backslash).
# ``_UNKNOWN`` marks text only known when the command runs — an unset
# variable, a command substitution's output. ``_CURRENT_DIR`` marks
# ``$(pwd)``, which is known: it is wherever the walk has got to.
_UNKNOWN = '\x00'
_CURRENT_DIR = '\x01'

_LINE_CONTINUATION = re.compile(r'\\\r?\n')
_PWD_SUBSTITUTION = re.compile(r'\$\(\s*pwd\s*\)|`\s*pwd\s*`')
# A URL is an address, not a file. It ends where a JSON/code delimiter starts
# so a path written right after it (``{"url":"http://x","script":"/Users/…"}``)
# is still seen. ``file://`` URLs ARE files: their path is kept.
_URL = re.compile(r'\b([A-Za-z][A-Za-z0-9+.-]*)://([^\s,;<>(){}\[\]|]*)')
# Statement separators — newline included (a ``cd`` on its own line moves the
# shell exactly like one after ``&&``) — and parentheses. A lone ``&`` that is
# part of a redirection (``2>&1``, ``&>``) is not a separator.
_STRUCTURE = re.compile(r'&&|\|\||;;|(?<![<>])&(?![>&])|[;|\n()]')
_VARIABLE = re.compile(r'\$\{([A-Za-z_]\w*)\}|\$\{[^}]*\}|\$([A-Za-z_]\w*)|\$[0-9@*#?$!-]')
# Where one path ends and another begins INSIDE a single shell word:
# ``--config=/x``, ``PYTHONPATH=a:../b``, ``open(/x)``, ``{"k":"/x"}``,
# ``base+/x``. Splitting only ever ADDS candidates — an absolute or climbing
# word is still checked whole — so a separator that is really part of a path
# name (``/task/a:/../../..``) can't hide an escape.
_PIECE_SEPARATOR = re.compile(r'[=:,;+()\[\]{}<>|&]')
# ``-I/Users/x``, ``-L../lib``: a path glued to a one-letter option.
_GLUED_OPTION = re.compile(r'^-{1,2}[A-Za-z]([/~].*|\.\.(?:/.*)?)$')
# ``open(f"/Users/x")`` loses its quotes to de-obfuscation and reads as
# ``f/Users/x``; a string-literal prefix glued to a home-tree path is that path.
_STRING_PREFIXED = re.compile(
    r'^[rRbBfFuU]{1,2}((?:%s).*)$' % '|'.join(map(re.escape, _USER_SPACE_PREFIXES)),
)
_ASSIGNMENT = re.compile(r'^([A-Za-z_]\w*)=(.*)$', re.ASCII)
_REDIRECTION = re.compile(r'^\d*(?:>>?|<|&>)(.*)$')
# Words that open or close a compound command; the program comes after them.
_COMPOUND_KEYWORDS = frozenset({
    '{', '}', '!', 'if', 'then', 'elif', 'else', 'fi',
    'do', 'done', 'while', 'until', 'esac',
})
_DECLARATION_PROGRAMS = frozenset({'export', 'declare', 'local', 'readonly', 'typeset'})
_CD_OPTIONS = frozenset({'-P', '-L', '-e', '-@', '--'})
_LN_OPTIONS_WITH_VALUE = frozenset({'--target-directory', '--suffix'})


def _prepare_command(command: str) -> str:
    text = str(command or '').replace(_UNKNOWN, '').replace(_CURRENT_DIR, '')
    text = _LINE_CONTINUATION.sub(' ', text)
    text = _PWD_SUBSTITUTION.sub(_CURRENT_DIR, text)
    text = deobfuscate_command(text)
    return _URL.sub(
        lambda match: match.group(2) if match.group(1).lower() == 'file' else ' ',
        text,
    )


def _shell_structure(text: str) -> list[tuple[str, str]]:
    """The statements of ``text`` in order, as ``('run', statement)`` events,
    with ``('enter', '')`` / ``('leave', '')`` around a ``( … )`` subshell.

    A ``(`` opens a subshell only where a command could start; any other
    parenthesis (``open(x)``, ``$(…)``, ``arr=(…)``) stays part of the
    statement's text but is still tracked, so its ``)`` can't close a subshell
    early."""
    events: list[tuple[str, str]] = []
    parens: list[bool] = []  # True for a subshell, False for any other paren
    pending = ''
    position = 0
    for match in _STRUCTURE.finditer(text):
        pending += text[position:match.start()]
        position = match.end()
        token = match.group(0)
        if token == '(':
            opens_subshell = all(word in _COMPOUND_KEYWORDS for word in pending.split())
            parens.append(opens_subshell)
            if opens_subshell:
                events.extend((('run', pending), ('enter', '')))
                pending = ''
            else:
                pending += token
        elif token == ')':
            if parens and parens.pop():
                events.extend((('run', pending), ('leave', '')))
                pending = ''
            else:
                pending += token
        else:
            events.append(('run', pending))
            pending = ''
    events.append(('run', pending + text[position:]))
    return events


class _ShellPosition:
    """Where the simulated shell is: its directory, the variables it has set,
    and the ``pushd`` stack — each restored when a subshell ends."""

    def __init__(self, cwd: str) -> None:
        self.cwd = cwd
        self.variables: dict[str, str] = {}
        self.pushed: list[str] = []
        self._saved: list[tuple[str, dict[str, str], list[str]]] = []

    def enter_subshell(self) -> None:
        self._saved.append((self.cwd, dict(self.variables), list(self.pushed)))

    def leave_subshell(self) -> None:
        if self._saved:
            self.cwd, self.variables, self.pushed = self._saved.pop()

    def expand(self, text: str) -> str:
        def value(match: re.Match) -> str:
            name = match.group(1) or match.group(2)
            if name in self.variables:
                return self.variables[name]
            if name == 'PWD':
                return self.cwd
            return _UNKNOWN

        return _VARIABLE.sub(value, text.replace(_CURRENT_DIR, self.cwd))

    def assign(self, word: str) -> None:
        match = _ASSIGNMENT.match(word)
        if match:
            # Already expanded, so a ``$`` left in the value is a command
            # substitution whose output can't be known here.
            value = match.group(2)
            self.variables[match.group(1)] = _UNKNOWN if '$' in value else value

    def change_directory(self, target: str) -> None:
        # ``cd -`` and a bare ``cd`` need OLDPWD / HOME tracking, and a target
        # built from an unknown variable can't be resolved: stay put.
        if not target or target == '-' or _UNKNOWN in target:
            return
        self.cwd = _normalize(os.path.expanduser(target), self.cwd)

    def apply(self, words: list[str]) -> None:
        """Carry a statement's effect on directory and variables forward."""
        index = program_token_index(words)
        if index >= len(words):
            # Nothing but ``NAME=value`` words: they stay set.
            for word in words:
                self.assign(word)
            return
        program, arguments = words[index], words[index + 1:]
        if program in _DECLARATION_PROGRAMS:
            for word in arguments:
                self.assign(word)
        elif program == 'cd':
            self.change_directory(_directory_operand(arguments))
        elif program == 'pushd':
            target = _directory_operand(arguments)
            if target:
                self.pushed.append(self.cwd)
                self.change_directory(target)
        elif program == 'popd' and self.pushed:
            self.cwd = self.pushed.pop()


def _statement_words(statement: str) -> list[str]:
    words = statement.split()
    start = 0
    while start < len(words) and words[start] in _COMPOUND_KEYWORDS:
        start += 1
    return words[start:]


def _redirection_width(word: str) -> int:
    """How many words a redirection takes: ``2>/dev/null`` one, ``> out`` two."""
    return 1 if _REDIRECTION.match(word).group(1) else 2


def _directory_operand(arguments: list[str]) -> str:
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if _REDIRECTION.match(argument):
            index += _redirection_width(argument)
        elif argument in _CD_OPTIONS:
            index += 1
        else:
            return argument
    return ''


@dataclass
class _LinkArguments:
    """What an ``ln`` command's arguments say."""

    symbolic: bool = False
    target_directory: str = ''
    operands: list[int] = field(default_factory=list)

    def read_option(self, arguments: list[str], index: int) -> int:
        """Record the option at ``index``; return how many words it takes."""
        following = arguments[index + 1] if index + 1 < len(arguments) else ''
        if arguments[index].startswith('--'):
            return self._read_long_option(arguments[index], following)
        return self._read_short_options(arguments[index], following)

    def _read_long_option(self, option: str, following: str) -> int:
        name, equals, value = option.partition('=')
        self.symbolic = self.symbolic or name == '--symbolic'
        takes_next = name in _LN_OPTIONS_WITH_VALUE and not equals
        if name == '--target-directory':
            self.target_directory = following if takes_next else value
        return 2 if takes_next else 1

    def _read_short_options(self, option: str, following: str) -> int:
        # ``-sfn`` bundles flags; ``-t DIR`` / ``-tDIR`` and ``-S SUFFIX`` end
        # the bundle, taking the rest of the word or the next one.
        for offset, flag in enumerate(option[1:], start=2):
            self.symbolic = self.symbolic or flag == 's'
            if flag in 'tS':
                attached = option[offset:]
                if flag == 't':
                    self.target_directory = attached or following
                return 1 if attached else 2
        return 1


def _parse_link_arguments(arguments: list[str]) -> _LinkArguments:
    parsed = _LinkArguments()
    options_done = False
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if _REDIRECTION.match(argument):
            index += _redirection_width(argument)
        elif options_done or argument == '-' or not argument.startswith('-'):
            parsed.operands.append(index)
            index += 1
        elif argument == '--':
            options_done = True
            index += 1
        else:
            index += parsed.read_option(arguments, index)
    return parsed


def _resolve_known(path: str, cwd: str) -> str:
    return '' if _UNKNOWN in path else _normalize(os.path.expanduser(path), cwd)


def _symlink_target_bases(words: list[str], program_index: int, cwd: str) -> dict[int, tuple[str, ...]]:
    """``{word index: directories}`` for the TARGETS of ``ln -s``.

    A relative symlink target is resolved from the directory the link lives
    in, not from the shell's directory: ``cd lib && ln -s ../../shared/data
    tests/data`` points at ``lib/tests/../../shared/data``. Without the disk
    there is no telling whether ``tests/data`` is an existing directory the
    link would go inside; reading it as the link itself is the cautious choice
    — the link's directory is then one level higher, so a climbing target can
    only land further out, never further in."""
    arguments = words[program_index + 1:]
    parsed = _parse_link_arguments(arguments)
    if not parsed.symbolic or not parsed.operands:
        return {}
    if parsed.target_directory:
        targets = parsed.operands
        link_directories = [_resolve_known(parsed.target_directory, cwd)]
    elif len(parsed.operands) == 1:
        targets, link_directories = parsed.operands, [cwd]
    else:
        targets = parsed.operands[:-1]
        link_word = arguments[parsed.operands[-1]]
        link = _resolve_known(link_word, cwd)
        # Several targets, or a trailing ``/``, put the links INTO that
        # directory; otherwise the operand names the link itself.
        into_directory = len(targets) > 1 or link_word.endswith('/')
        link_directories = [link if into_directory else os.path.dirname(link)]
    bases = tuple(directory for directory in link_directories if directory) or (cwd,)
    return {program_index + 1 + target: bases for target in targets}


def _word_candidates(word: str) -> list[str]:
    """The paths one shell word may name: each piece between in-word
    separators, then the whole word when it could be a path that merely
    CONTAINS a separator — one that is absolute, or climbs with ``..``.

    Any other whole word is left out: ``>/dev/null`` or
    ``JAVA_HOME=$(/usr/libexec/java_home`` resolved as one relative path from
    ``/tmp`` would read as an escape that isn't there."""
    pieces = [piece for piece in _PIECE_SEPARATOR.split(word) if piece]
    could_hide_an_escape = word.startswith(('/', '~')) or '..' in word.split('/')
    if could_hide_an_escape and word not in pieces:
        pieces.append(word)
    return pieces


def _checkable_path(candidate: str) -> str:
    """The path in ``candidate`` worth checking, or ``''``.

    A candidate STARTING with an unknown value can't be resolved and is
    skipped; one that merely ends in one is checked up to it
    (``/Users/me/$f`` → ``/Users/me/``)."""
    if candidate.startswith(_UNKNOWN):
        return ''
    candidate = candidate.split(_UNKNOWN, 1)[0]
    if candidate.startswith('-'):
        glued = _GLUED_OPTION.match(candidate)
        if not glued:
            return ''
        candidate = glued.group(1)
    prefixed = _STRING_PREFIXED.match(candidate)
    if prefixed:
        candidate = prefixed.group(1)
    if candidate.startswith(('/', '~')):
        return candidate if len(candidate) >= 2 else ''
    if '/' in candidate or candidate == '..':
        return candidate
    return ''


class _Sandbox:
    """The roots a command must stay inside, and the files exempt from them."""

    def __init__(self, roots: list[str], allowed: set[str]) -> None:
        self._roots = roots
        self._allowed = allowed

    def _contains(self, resolved: str) -> bool:
        return resolved in self._allowed or any(_is_within(resolved, root) for root in self._roots)

    def escaping_path(self, candidate: str, bases: tuple[str, ...], *, relative_too: bool = True) -> str:
        """The path ``candidate`` names if it leaves the sandbox, else ``''``."""
        path = _checkable_path(candidate)
        if not path:
            return ''
        if path.startswith(('/', '~')):
            resolved = os.path.normpath(os.path.expanduser(path))
            return path if resolved.startswith(_USER_SPACE_PREFIXES) and not self._contains(resolved) else ''
        if not relative_too:
            return ''
        climbs = '..' in path.split('/')
        for base in bases:
            resolved = _normalize(path, base)
            # Judged like an absolute path unless it climbs: after ``cd /tmp``
            # (a system folder, not flagged) ``application/json`` is just
            # ``/tmp/application/json``.
            if not self._contains(resolved) and (climbs or resolved.startswith(_USER_SPACE_PREFIXES)):
                return path
        return ''

    def escape_in_statement(self, words: list[str], cwd: str) -> str:
        program_index = program_token_index(words)
        target_bases: dict[int, tuple[str, ...]] = {}
        if program_index < len(words) and words[program_index] == 'ln':
            target_bases = _symlink_target_bases(words, program_index, cwd)
        candidates = (
            (candidate, target_bases.get(index, (cwd,)))
            for index, word in enumerate(words)
            for candidate in _word_candidates(word)
        )
        return next((path for path in (self.escaping_path(c, b) for c, b in candidates) if path), '')

    def escape_in_shell(self, shell_text: str, position: _ShellPosition) -> str:
        for kind, statement in _shell_structure(shell_text):
            if kind == 'enter':
                position.enter_subshell()
            elif kind == 'leave':
                position.leave_subshell()
            else:
                words = _statement_words(position.expand(statement))
                offending = self.escape_in_statement(words, position.cwd)
                if offending:
                    return offending
                position.apply(words)
        return ''

    def escape_in_bodies(self, bodies: list[str], position: _ShellPosition) -> str:
        # cwd-independent by construction: only absolute paths are considered.
        candidates = (
            candidate
            for body in bodies
            for word in position.expand(body).split()
            for candidate in _word_candidates(word)
        )
        return next(
            (path for path in (
                self.escaping_path(c, (position.cwd,), relative_too=False) for c in candidates
            ) if path),
            '',
        )


def classify_command_sandbox(
    command: str,
    cwd: str,
    additional_dirs: tuple[str, ...] | list[str] = (),
    allowed_paths: tuple[str, ...] | list[str] = (),
) -> tuple[bool, str]:
    """Return ``(outside, offending_path)`` for a shell command's path args.

    Companion to ``classify_tool_input_sandbox`` for Bash: a ``grep`` / ``cat``
    / ``python -c "open('…')"`` naming a path that escapes the sandbox (another
    repo, ``~/.ssh``, a ``../../`` climb-out) is flagged so the UI can warn +
    withhold a remembered grant. Hardened against quote-splitting, backslash
    escaping and ``$HOME`` indirection; the sandbox roots + ``allowed_paths``
    allow-list are exempt, so ordinary ``git``/``ls``/``mvn`` never trips it.

    The command is walked statement by statement — ``&&``/``||``/``;``/``|``
    AND newlines — the way the shell would run it:

    * ``cd`` / ``pushd`` / ``popd`` move the directory later relative paths
      resolve from, so an escape split across ``cd ..`` hops is caught and an
      in-task ``cd`` followed by relative paths is not mistaken for one;
    * a ``( … )`` subshell's ``cd`` and variables end with it;
    * ``NAME=value`` / ``export NAME=value`` (including ``R=$PWD``) are
      substituted into later ``$NAME`` / ``${NAME}``; an unknown variable is
      not guessed at;
    * URLs are addresses, not paths;
    * a relative ``ln -s`` target resolves from the link's directory.

    Heredoc bodies are DATA, not arguments — a file being written, a patch, a
    SQL script. Scanning them with the shell rules made every relative path
    MENTIONED in prose ("see ../../docs/setup.md") read as a path being opened.
    They are still scanned, but only for ABSOLUTE / home-tree paths: that keeps
    the case worth catching (``open('/Users/me/.ssh/id_rsa')`` smuggled into a
    heredoc) while dropping the prose false positives. Known residual: a
    RELATIVE climb-out inside a body is not flagged — accepted, it is a warning
    layer and the docker sandbox is the structural boundary.

    Static-only by nature: a path computed at runtime (a variable set by a
    program, base64, fetched) is invisible here — the docker setting is the
    OS-level guarantee."""
    norm_roots = _effective_roots(cwd, additional_dirs)
    if not norm_roots:
        return False, ''
    sandbox = _Sandbox(norm_roots, {_normalize(p, cwd) for p in allowed_paths if p})
    shell_text, heredoc_bodies = split_heredoc_bodies(_prepare_command(command))
    position = _ShellPosition(os.path.normpath(cwd) if cwd else cwd)
    offending = (
        sandbox.escape_in_shell(shell_text, position)
        or sandbox.escape_in_bodies(heredoc_bodies, position)
    )
    return bool(offending), offending

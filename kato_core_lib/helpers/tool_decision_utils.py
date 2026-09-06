"""Server-side mirror of ``webserver/ui/src/utils/permissionEnvelope.js``'s
command-signature algorithm.

Remembered tool-permission decisions ("Allow always" / "Deny always")
are backend-owned (see ``tool_decision_store.py``) — the browser must
never be the one deciding whether a pending ask gets auto-resolved, so
the signature that keys a remembered decision has to be computable
here, not just in the client. This is a deliberate line-for-line port
of the JS implementation; keep the two in sync (the JS file's tests
double as the spec for the quote/heredoc-splitting edge cases).
"""
from __future__ import annotations

import re

from agent_core_lib.agent_core_lib.helpers.command_introspection import (
    program_token_index,
)
from agent_core_lib.agent_core_lib.helpers.command_policy import (
    git_revert_breadth,
    git_subcommand_of,
)

# Tools whose remembered decision is keyed by the COMMAND, not the tool
# name — so "Allow always" on `mvn ...` does NOT silently allow `docker ...`.
COMMAND_KEYED_TOOLS = frozenset({'Bash'})

# Pure-navigation / setup builtins that get prepended to almost every
# command (``cd <task-workspace> && ...``). Keying on these would collapse
# everything into one entry, so they're treated as noise unless a command
# is ONLY navigation (then key on it so a bare `cd` still works).
# NOTE: ``source``/``.`` are NOT here -- see _TARGET_FOLDING_PROGRAMS below.
# Unlike ``cd``, they execute arbitrary file content in the current shell;
# treating them as noise let `source ./setup_venv.sh` and `source ./evil.sh`
# (or `cd project && source venv/bin/activate` vs `cd /tmp && source
# /tmp/payload.sh`) collapse to the identical bare "source" / "cd source"
# signature -- approving one silently blessed the other forever.
_NOISE_PROGRAMS = frozenset({'cd', 'pushd', 'popd', 'export'})

# Pure output-shaping pipes Claude tacks onto the END of a command to
# truncate/summarize what it reads back (`... | head -30`, `| tail -20`,
# `| wc -l`). These change nothing about what the command actually DOES —
# unlike a "new program tacked onto an allowed one" that genuinely needs
# its own re-approval (test_chain_keeps_every_program), a different
# truncation choice on an otherwise-identical, already-approved command
# was silently re-prompting every time (operator report: approved
# `python -m pytest ...` once, the next turn appended `| head -30` and
# the remembered decision no longer matched). Deliberately a SHORT,
# hand-picked allowlist of programs that ONLY read stdin and print to
# stdout — NOT a general "trust anything after a pipe" rule. Do not add
# anything here that can affect program behavior, WRITE A FILE, or
# exfiltrate data. In particular `sort` (`-o FILE`, `--compress-program=PROG`)
# and `uniq` (`uniq IN OUT`) can write files / run programs, so they are
# NOT here — folding them let `<approved> && sort -o .git/hooks/pre-commit
# payload` ride an already-remembered signature with no re-prompt. Likewise
# grep/curl/xargs/tee/sh -c/eval/nc must keep re-prompting.
_OUTPUT_SHAPING_PROGRAMS = frozenset({'head', 'tail', 'wc'})

# Privilege-escalation wrappers -- the OPPOSITE problem from _NOISE_PROGRAMS:
# dropping these would be wrong (running AS root is exactly the part that
# matters), but keying on the bare wrapper name is just as unsafe -- `sudo npm
# install`, `sudo rm -rf /`, and `sudo cat /etc/shadow` would all collapse to
# the single signature "sudo", so approving any ONE of them once would
# silently auto-approve every future `sudo <anything>` forever. Fold the
# escalation command AND its target into one signature entry instead (see
# _program_of_segment) so each stays independently remembered.
_PRIVILEGE_ESCALATION_PROGRAMS = frozenset({'sudo', 'doas', 'pkexec', 'su'})

# ``source``/``.`` (its POSIX alias) execute arbitrary shell code from a
# file -- same "the target is what matters" problem as privilege escalation,
# just a script path instead of a root shell. Folded the same way.
_SOURCE_EXECUTION_PROGRAMS = frozenset({'source', '.'})

# Union used by _program_of_segment's fold check -- both wrapper classes get
# identical treatment (fold wrapper + cleaned target token).
_TARGET_FOLDING_PROGRAMS = _PRIVILEGE_ESCALATION_PROGRAMS | _SOURCE_EXECUTION_PROGRAMS

_LEADING_WRAPPER_RE = re.compile(r'^[\s($`]+')
_TRAILING_WRAPPER_RE = re.compile(r'[)`]+$')
_PATH_PREFIX_RE = re.compile(r'^.*/')
# ASCII heredoc-terminator char class, mirroring permissionEnvelope.js's
# ``/[A-Za-z0-9_]/`` exactly (see _match_heredoc_start).
_HEREDOC_TERM_CHAR = re.compile(r'\w', re.ASCII)

# Escalation-wrapper option flags that take a SEPARATE argument (a principal,
# not a program): ``sudo -u root``, ``sudo --group wheel``. Blindly folding
# the wrapper with ``tokens[i+1]`` made ``sudo -u root bash evil.sh`` and
# ``sudo -u postgres psql`` BOTH key on the collapse-prone ``sudo -u`` — so
# approving one blessed running ANY program as ANY user. We skip these flags
# (and their argument) to reach the REAL target program instead.
_ESCALATION_ARG_FLAGS = frozenset({'-u', '--user', '-g', '--group'})
_ESCALATION_ARG_FLAG_EQ = ('-u=', '--user=', '-g=', '--group=')


def is_command_keyed_tool(tool_name: str) -> bool:
    return str(tool_name or '') in COMMAND_KEYED_TOOLS


def command_of(tool_input: dict) -> str:
    """The full command an execution tool will run (whitespace-normalized)."""
    if not isinstance(tool_input, dict):
        return ''
    return re.sub(r'\s+', ' ', str(tool_input.get('command') or '')).strip()


def _clean_token(token: str) -> str:
    return _PATH_PREFIX_RE.sub('', _TRAILING_WRAPPER_RE.sub('', token))


def _escalation_target_token(tokens: list[str], wrapper_index: int) -> str:
    """The cleaned target-program token folded onto an escalation wrapper
    (``sudo``/``doas``/``pkexec``/``su``/``source``/``.``), or ``''`` when the
    wrapper has no target. Skips the wrapper's own principal-taking flags
    (``-u root``, ``--group wheel``) so ``sudo -u root bash`` folds to
    ``sudo bash`` (not the collapse-prone ``sudo -u``), then steps through any
    benign wrapper (``sudo -u root timeout 10 bash`` → ``sudo bash``) via the
    shared ``program_token_index``."""
    j = wrapper_index + 1
    while j < len(tokens):
        token = tokens[j]
        if token in _ESCALATION_ARG_FLAGS:
            j += 2  # skip the flag AND its separate argument (the principal)
            continue
        if token.startswith(_ESCALATION_ARG_FLAG_EQ):
            j += 1  # `--user=root` — one token, skip it whole
            continue
        break
    index = program_token_index(tokens, j)
    if index >= len(tokens):
        return ''
    return _clean_token(tokens[index])


def _program_of_segment(segment: str) -> str:
    """The program a single shell segment invokes, basename-only. Leading
    ``VAR=val`` env assignments and benign wrapper programs
    (``env``/``timeout``/``nice``/…) are stepped through via the shared
    ``program_token_index`` so ``timeout 300 python`` keys on ``python`` (the
    Action Guard classifies the same command the same way) — otherwise every
    ``timeout <x>`` collapsed to the bare ``timeout`` key and one remembered
    ``timeout`` grant rode ``timeout bash evil.sh``. A privilege-escalation
    wrapper (``sudo``, ...) is folded together with its REAL target program
    (e.g. ``sudo npm``) rather than returned bare -- see
    _PRIVILEGE_ESCALATION_PROGRAMS / _escalation_target_token."""
    stripped = _LEADING_WRAPPER_RE.sub('', str(segment))
    tokens = [token for token in re.split(r'\s+', stripped) if token]
    i = program_token_index(tokens)
    if i >= len(tokens):
        return ''
    prog = _clean_token(tokens[i])
    if prog in _TARGET_FOLDING_PROGRAMS:
        target = _escalation_target_token(tokens, i)
        if target:
            return f'{prog} {target}'
    if prog == 'git':
        return _git_signature(segment, prog)
    return prog


def _git_signature(segment: str, prog: str) -> str:
    """Fold the git SUBCOMMAND (and revert breadth) into the remembered key.

    ``git`` alone is far too coarse a key now that the agent may revert
    files: ``git status`` and ``git restore .`` both reduced to ``git``, so
    one "always allow" on a read-only status check silently granted every
    future working-tree revert — a grant no operator would knowingly give.

    The breadth suffix draws the second line: approving ``git restore
    src/a.js`` must not also pre-approve ``git restore .``, which discards
    the whole uncommitted task with nothing to recover from.
    """
    subcommand = git_subcommand_of(segment)
    if not subcommand:
        return prog
    breadth = git_revert_breadth(segment)
    if breadth == 'whole-tree':
        return f'{prog} {subcommand} (whole tree)'
    return f'{prog} {subcommand}'


def _match_heredoc_start(command: str, i: int):
    """Recognizes a heredoc operator (``<<EOF``, ``<<-EOF``, ``<<'EOF'``,
    ``<<"EOF"``) starting at index ``i``. Returns ``(term, strip, next)`` on
    a match, or ``None``."""
    length = len(command)
    if i + 1 >= length or command[i] != '<' or command[i + 1] != '<':
        return None
    j = i + 2
    strip = False
    if j < length and command[j] == '-':
        strip = True
        j += 1
    while j < length and command[j] in (' ', '\t'):
        j += 1
    term = ''
    if j < length and command[j] in ("'", '"'):
        quote = command[j]
        j += 1
        start = j
        while j < length and command[j] != quote:
            j += 1
        if j >= length:
            return None
        term = command[start:j]
        j += 1
    else:
        start = j
        # ASCII ``[A-Za-z0-9_]`` (not Unicode ``str.isalnum``) so this matches
        # the JS mirror's ``/[A-Za-z0-9_]/`` heredoc-terminator scan exactly —
        # a Unicode-aware check made the two compute different signatures for a
        # terminator carrying a non-ASCII letter.
        while j < length and _HEREDOC_TERM_CHAR.match(command[j]):
            j += 1
        term = command[start:j]
    return (term, strip, j) if term else None


def _split_top_level_shell_segments(command: str) -> list[str]:
    """Splits a RAW (not whitespace-collapsed) command into its top-level
    ``&&``/``||``/``;``/``|`` segments, skipping any of those characters
    that fall inside a quoted argument or a heredoc body instead of acting
    as a real shell separator.

    Without this, a command whose quoted/heredoc'd content happens to
    contain ``;``/``|``/``&&`` — e.g. a commit made via
    ``git commit -m "$(cat <<'EOF' ...multi-line message... EOF)"`` —
    fractures into a DIFFERENT, unstable signature every time despite
    being "the same" command, so a remembered decision silently stops
    matching. See ``permissionEnvelope.js`` for the identical JS version.
    """
    segments: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    in_backtick = False
    heredoc_term = None
    heredoc_strip = False
    line_buf: list[str] = []
    length = len(command)
    i = 0
    while i < length:
        ch = command[i]
        if heredoc_term is not None:
            current.append(ch)
            if ch == '\n':
                line = ''.join(line_buf)
                if heredoc_strip:
                    line = line.lstrip('\t')
                if line.strip() == heredoc_term:
                    heredoc_term = None
                line_buf = []
            else:
                line_buf.append(ch)
            i += 1
            continue
        if in_single:
            current.append(ch)
            if ch == "'":
                in_single = False
            i += 1
            continue
        heredoc = _match_heredoc_start(command, i)
        if heredoc:
            term, strip, next_i = heredoc
            current.append(command[i:next_i])
            heredoc_term = term
            heredoc_strip = strip
            line_buf = []
            i = next_i
            continue
        if in_double or in_backtick:
            if ch == '\\' and i + 1 < length:
                current.append(ch)
                current.append(command[i + 1])
                i += 2
                continue
            current.append(ch)
            if (in_double and ch == '"') or (in_backtick and ch == '`'):
                in_double = False
                in_backtick = False
            i += 1
            continue
        if ch == "'":
            in_single = True
            current.append(ch)
            i += 1
            continue
        if ch == '"':
            in_double = True
            current.append(ch)
            i += 1
            continue
        if ch == '`':
            in_backtick = True
            current.append(ch)
            i += 1
            continue
        if ch == '&' and i + 1 < length and command[i + 1] == '&':
            segments.append(''.join(current))
            current = []
            i += 2
            continue
        if ch == '|' and i + 1 < length and command[i + 1] == '|':
            segments.append(''.join(current))
            current = []
            i += 2
            continue
        if ch in (';', '|'):
            segments.append(''.join(current))
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    segments.append(''.join(current))
    return segments


def command_signature_of(command: str) -> str:
    """The remembered KEY for a command: the set of programs it actually
    runs, path/arg/cwd-independent, so the same `mvn verify` matches
    across task folders. ALL programs in a chain are included (deduped,
    in order) so `mvn ... && rm -rf ...` never matches a remembered bare
    `mvn` — a new program tacked onto an allowed one re-prompts instead
    of riding through. The one exception is _OUTPUT_SHAPING_PROGRAMS
    (`head`/`tail`/`wc`/`sort`/`uniq`) — read-only truncation/summary
    pipes folded into noise like `cd`, so `... | head -30` this turn and
    `... | tail -20` next turn still match the same remembered decision."""
    raw = str(command or '')
    if not raw.strip():
        return ''
    meaningful: list[str] = []
    noise: list[str] = []
    for segment in _split_top_level_shell_segments(raw):
        prog = _program_of_segment(segment)
        if not prog:
            continue
        is_noise = prog in _NOISE_PROGRAMS or prog in _OUTPUT_SHAPING_PROGRAMS
        bucket = noise if is_noise else meaningful
        if prog not in bucket:
            bucket.append(prog)
    # A non-empty command MUST never yield an empty signature: an empty key
    # collapses a command-keyed Bash decision to the bare tool name `Bash`,
    # i.e. a tool-WIDE "allow all bash" grant.
    joined = ' '.join(meaningful if meaningful else noise)
    return joined or re.sub(r'\s+', ' ', raw).strip()


def command_programs_of(command: str) -> list[str]:
    """The distinct programs a command runs, in order.

    The per-decision unit. ``command_signature_of`` joins these into ONE key,
    which made every new COMBINATION of already-approved programs a brand-new
    decision: ``awk grep`` and ``awk grep sed`` are unrelated keys, so the
    store grew combinatorially and never converged — an operator with 840
    remembered decisions was still being asked on almost every turn, which is
    how a safety prompt turns into something people click through.

    Remembering per program is also what the UI has always promised: "Bash
    entries are per program (e.g. mvn, docker) — allowing one program never
    allows another."
    """
    raw = str(command or '')
    if not raw.strip():
        return []
    meaningful: list[str] = []
    noise: list[str] = []
    for segment in _split_top_level_shell_segments(raw):
        prog = _program_of_segment(segment)
        if not prog:
            continue
        is_noise = prog in _NOISE_PROGRAMS or prog in _OUTPUT_SHAPING_PROGRAMS
        bucket = noise if is_noise else meaningful
        if prog not in bucket:
            bucket.append(prog)
    # Same fallback as the signature: a non-empty command must never yield an
    # empty set of programs, or the decision collapses to a tool-WIDE grant.
    return meaningful or noise or [re.sub(r'\s+', ' ', raw).strip()]


def decision_programs_for(tool_name: str, tool_input: dict) -> list[str]:
    """The programs to remember/recall for a request; ``[]`` for
    tool-level (non command-keyed) tools."""
    if not is_command_keyed_tool(tool_name):
        return []
    return command_programs_of(command_of(tool_input))


def decision_command_for(tool_name: str, tool_input: dict) -> str:
    """The (tool, command-signature) pair to remember/recall for a
    request: the program signature for command-keyed tools, else ''
    (tool-level)."""
    if not is_command_keyed_tool(tool_name):
        return ''
    return command_signature_of(command_of(tool_input))


def _is_answerable_question_item(item: object) -> bool:
    return (
        isinstance(item, dict)
        and isinstance(item.get('question'), str)
        and bool(item.get('question').strip())
        and isinstance(item.get('options'), list)
    )


def is_answerable_question(tool_input: dict) -> bool:
    """Mirrors ``answerableQuestion.js``'s ``extractAnswerableQuestions``:
    detects an "ask the operator a multiple-choice question" tool call by
    its SHAPE (a non-empty ``questions`` array with prompt+options
    entries), not by tool name, so it matches Claude's ``AskUserQuestion``
    or any future agent backend emitting the same payload shape.

    These must never be auto-resolved from a remembered decision — each
    question is a distinct clarification the agent wants a human to
    answer, never a repeat of a previously-approved action.
    """
    if not isinstance(tool_input, dict):
        return False
    questions = tool_input.get('questions')
    if not isinstance(questions, list) or not questions:
        return False
    return any(_is_answerable_question_item(item) for item in questions)

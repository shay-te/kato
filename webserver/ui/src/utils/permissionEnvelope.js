// ``control_request`` nests under `request`; older ``permission_request`` is flat.
//
// ``outsideSandbox``/``outsidePath`` are stamped by the streaming layer
// (claude_core_lib sandbox_scope) when the ask reaches a filesystem path
// outside the task folder + its --add-dir set. The modal uses them to
// warn loudly AND to withhold the remembered-approval ("allow always")
// scope — a persisted grant for an out-of-sandbox path is exactly what
// must never be one click away.
export function unpackPermissionEnvelope(raw) {
  const nested = (raw && typeof raw.request === 'object' && raw.request) || {};
  return {
    // Which task this ask belongs to — stamped by the global pending-permissions
    // feed so a cross-task modal can name it in the title. '' for the focused
    // task's own SSE envelope (the operator already knows which tab they're on).
    taskId: String(raw?.task_id || nested.task_id || ''),
    // Free-text task summary stamped alongside ``task_id`` on the global
    // feed; the focused-task path supplies it via prop. Title shows it
    // beside the id ("<UNA-2763> — <library add collaborators>").
    taskSummary: String(raw?.task_summary || nested.task_summary || ''),
    requestId: String(raw?.request_id || raw?.id || nested.request_id || nested.id || ''),
    toolName: String(
      raw?.tool_name || raw?.tool
      || nested.tool_name || nested.tool || 'tool',
    ),
    toolInput: raw?.input || nested.input || {},
    outsideSandbox: !!(raw?.outside_sandbox || nested.outside_sandbox),
    outsidePath: String(raw?.outside_path || nested.outside_path || ''),
    // Action Guard risk classification stamped by the webserver on a
    // control_request (category / decision / reason / rule_id), or null.
    // Purely additive — old envelopes without it behave exactly as before.
    actionGuard: (raw?.action_guard || nested.action_guard || null),
  };
}

// Action Guard categories whose remembered "allow always" must never be one
// click away — a persisted grant for reading credentials / exfiltrating /
// remote-exec / sandbox-escape is exactly what the guard exists to stop.
// NOTE: ``network_tool`` (WebFetch/WebSearch) is intentionally NOT here —
// it's a dual-use research tool, so approving it once may be remembered.
// The dangerous upload/reverse-shell patterns are ``network_exfil``.
const HIGH_RISK_ACTION_GUARD = new Set([
  'credential_read', 'network_exfil', 'remote_exec', 'sandbox_escape',
]);

export function isHighRiskActionGuard(actionGuard) {
  return !!(
    actionGuard
    && HIGH_RISK_ACTION_GUARD.has(String(actionGuard.category || ''))
  );
}

// Tools whose remembered decision is keyed by the COMMAND, not the tool
// name — so "Allow always" on `mvn …` does NOT silently allow `docker …`.
// Each distinct command gets its own entry in Settings → Permissions.
const COMMAND_KEYED_TOOLS = new Set(['Bash']);

export function isCommandKeyedTool(toolName) {
  return COMMAND_KEYED_TOOLS.has(String(toolName || ''));
}

// The full command an execution tool will run (whitespace-normalized), or
// ''. Used for DISPLAY (the modal shows the real command); the remembered
// key uses commandSignatureOf instead.
export function commandOf(toolInput) {
  if (!toolInput || typeof toolInput !== 'object') { return ''; }
  return String(toolInput.command || '').replace(/\s+/g, ' ').trim();
}

// Pure-navigation / setup builtins that get prepended to almost every
// command (`cd <task-workspace> && …`, `export JAVA_HOME=… && …`). Keying on
// these would collapse everything into one entry — effectively a tool-wide
// allow — so they're treated as noise and dropped from the signature unless
// a command is ONLY navigation (then we key on it so a bare `cd` still works).
// NOTE: `source`/`.` are NOT here — see TARGET_FOLDING_PROGRAMS below. Unlike
// `cd`, they execute arbitrary file content in the current shell; treating
// them as noise let `source ./setup_venv.sh` and `source ./evil.sh` (or
// `cd project && source venv/bin/activate` vs `cd /tmp && source /tmp/payload.sh`)
// collapse to the identical bare "source" / "cd source" signature.
const NOISE_PROGRAMS = new Set(['cd', 'pushd', 'popd', 'export']);

// Privilege-escalation wrappers — the OPPOSITE problem from NOISE_PROGRAMS:
// dropping these would be wrong (unlike `cd`, running AS root is exactly the
// part that matters), but keying on the bare wrapper name is just as unsafe —
// `sudo npm install`, `sudo rm -rf /`, and `sudo cat /etc/shadow` would all
// collapse to the single signature "sudo", so approving any ONE of them once
// would silently auto-approve every future `sudo <anything>` forever. Fold
// the escalation command AND its target into one signature entry instead
// (see _programOfSegment) so each stays independently remembered.
const PRIVILEGE_ESCALATION_PROGRAMS = new Set(['sudo', 'doas', 'pkexec', 'su']);

// `source`/`.` (its POSIX alias) execute arbitrary shell code from a file —
// same "the target is what matters" problem as privilege escalation, just a
// script path instead of a root shell. Folded the same way: `source
// setup_venv.sh` and `source evil.sh` must never share a signature.
const SOURCE_EXECUTION_PROGRAMS = new Set(['source', '.']);

// Union used by _programOfSegment's fold check — both classes of wrapper get
// identical treatment (fold wrapper + cleaned target token).
const TARGET_FOLDING_PROGRAMS = new Set([
  ...PRIVILEGE_ESCALATION_PROGRAMS, ...SOURCE_EXECUTION_PROGRAMS,
]);

function _cleanToken(token) {
  // Drop trailing subshell closers/backticks, then strip any path → basename.
  return token.replace(/[)`]+$/, '').replace(/^.*\//, '');
}

// The program a single shell segment invokes, basename-only:
//   "JAVA_HOME=/x mvn -B verify" → "mvn"   "/usr/local/bin/docker ps" → "docker"
//   "./gradlew build"            → "gradlew"
//   "sudo npm install"           → "sudo npm" (see PRIVILEGE_ESCALATION_PROGRAMS)
function _programOfSegment(segment) {
  // Strip leading subshell openers / backticks so `(cd /x && mvn)`, `$(mvn)`
  // and `` `mvn` `` resolve to the real program, not a `(cd` / `$(mvn` token.
  const tokens = String(segment).replace(/^[\s($`]+/, '')
    .split(/\s+/).filter(Boolean);
  let i = 0;
  // Skip leading env-var assignments (FOO=bar) — they prefix, not invoke.
  while (i < tokens.length && /^[A-Za-z_][A-Za-z0-9_]*=/.test(tokens[i])) { i += 1; }
  const rawProg = tokens[i];
  if (!rawProg) { return ''; }
  const prog = _cleanToken(rawProg);
  if (TARGET_FOLDING_PROGRAMS.has(prog) && tokens[i + 1]) {
    // Not maximally precise about which token is a flag vs. the real
    // target (sudo's own flags can take arguments, e.g. `-u root`) — but
    // ANY additional token narrows the key vs. the bare wrapper name,
    // which is what actually matters: it makes the remembered grant
    // specific to (roughly) this target, not to "sudo, unconditionally".
    return `${prog} ${_cleanToken(tokens[i + 1])}`;
  }
  return prog;
}

// Recognizes a heredoc operator (``<<EOF``, ``<<-EOF``, ``<<'EOF'``,
// ``<<"EOF"``) starting at index ``i``. Returns ``{term, strip, next}`` on a
// match (``next`` is the index just past the delimiter) or ``null``.
function _matchHeredocStart(command, i) {
  if (command[i] !== '<' || command[i + 1] !== '<') { return null; }
  let j = i + 2;
  let strip = false;
  if (command[j] === '-') { strip = true; j += 1; }
  while (command[j] === ' ' || command[j] === '\t') { j += 1; }
  let term = '';
  if (command[j] === "'" || command[j] === '"') {
    const quote = command[j];
    j += 1;
    const start = j;
    while (j < command.length && command[j] !== quote) { j += 1; }
    if (j >= command.length) { return null; }
    term = command.slice(start, j);
    j += 1;
  } else {
    const start = j;
    while (j < command.length && /[A-Za-z0-9_]/.test(command[j])) { j += 1; }
    term = command.slice(start, j);
  }
  return term ? { term, strip, next: j } : null;
}

// Splits a RAW (not whitespace-collapsed — heredoc terminators must see real
// newlines) command into its top-level ``&&``/``||``/``;``/``|`` segments,
// skipping any of those characters that fall inside a quoted argument or a
// heredoc body instead of acting as a real shell separator.
//
// Without this, a command whose quoted/heredoc'd content happens to contain
// ``;``/``|``/``&&`` — e.g. a commit made via
// ``git commit -m "$(cat <<'EOF' ...multi-line message... EOF)"``, or any
// heredoc'd source file, `grep`/`sed` pattern with `|` alternation, or
// `python -c "a; b"` one-liner — fractures into a DIFFERENT, unstable
// signature every time despite being "the same" command. An operator's
// remembered "allow always" for `git`/`cat`/`grep` then silently stops
// matching and the permission modal re-prompts, which reads as "I keep
// approving and it keeps asking" (the reported bug: remembered decisions are
// keyed on this signature — see ``commandSignatureOf`` below).
function _splitTopLevelShellSegments(command) {
  const segments = [];
  let current = '';
  let inSingle = false;
  let inDouble = false;
  let inBacktick = false;
  let heredocTerm = null;
  let heredocStrip = false;
  let lineBuf = '';
  const len = command.length;
  let i = 0;
  while (i < len) {
    const ch = command[i];
    if (heredocTerm !== null) {
      current += ch;
      if (ch === '\n') {
        const line = heredocStrip ? lineBuf.replace(/^\t+/, '') : lineBuf;
        if (line.trim() === heredocTerm) { heredocTerm = null; }
        lineBuf = '';
      } else {
        lineBuf += ch;
      }
      i += 1;
      continue;
    }
    if (inSingle) {
      current += ch;
      if (ch === "'") { inSingle = false; }
      i += 1;
      continue;
    }
    // Heredoc redirection is recognized whether or not we're inside a
    // double-quote/backtick (real shells still parse `<<` inside `$(...)`
    // command substitution, which is exactly the `-m "$(cat <<'EOF' …)"`
    // shape a coding agent uses for multi-line commit messages).
    const heredoc = _matchHeredocStart(command, i);
    if (heredoc) {
      current += command.slice(i, heredoc.next);
      heredocTerm = heredoc.term;
      heredocStrip = heredoc.strip;
      lineBuf = '';
      i = heredoc.next;
      continue;
    }
    if (inDouble || inBacktick) {
      if (ch === '\\' && i + 1 < len) {
        current += ch + command[i + 1];
        i += 2;
        continue;
      }
      current += ch;
      if ((inDouble && ch === '"') || (inBacktick && ch === '`')) {
        inDouble = false;
        inBacktick = false;
      }
      i += 1;
      continue;
    }
    if (ch === "'") { inSingle = true; current += ch; i += 1; continue; }
    if (ch === '"') { inDouble = true; current += ch; i += 1; continue; }
    if (ch === '`') { inBacktick = true; current += ch; i += 1; continue; }
    if (ch === '&' && command[i + 1] === '&') {
      segments.push(current); current = ''; i += 2; continue;
    }
    if (ch === '|' && command[i + 1] === '|') {
      segments.push(current); current = ''; i += 2; continue;
    }
    if (ch === ';' || ch === '|') {
      segments.push(current); current = ''; i += 1; continue;
    }
    current += ch;
    i += 1;
  }
  segments.push(current);
  return segments;
}

// The remembered KEY for a command: the set of programs it actually runs,
// path/arg/cwd-independent, so the same `mvn verify` matches across task
// folders. ALL programs in a chain are included (deduped, in order) so that
// `mvn … && rm -rf …` ("mvn rm") never matches a remembered bare `mvn` — a
// new program tacked onto an allowed one re-prompts instead of riding through.
export function commandSignatureOf(command) {
  const raw = String(command || '');
  if (!raw.trim()) { return ''; }
  const meaningful = [];
  const noise = [];
  for (const segment of _splitTopLevelShellSegments(raw)) {
    const prog = _programOfSegment(segment);
    if (!prog) { continue; }
    const bucket = NOISE_PROGRAMS.has(prog) ? noise : meaningful;
    if (!bucket.includes(prog)) { bucket.push(prog); }
  }
  // A non-empty command MUST never yield an empty signature: an empty key
  // collapses a command-keyed Bash decision to the bare tool name `Bash`, i.e.
  // a tool-WIDE "allow all bash" grant — exactly what command-keying prevents.
  // Fall back to the whole normalized command (e.g. a pure `FOO=bar` env line,
  // or a redirect-only command) so the grant stays specific.
  return (meaningful.length ? meaningful : noise).join(' ') || raw.replace(/\s+/g, ' ').trim();
}

// The (tool, command-signature) pair to remember/recall for a request: the
// program signature for command-keyed tools, else '' (tool-level).
export function decisionCommandFor(toolName, toolInput) {
  return isCommandKeyedTool(toolName) ? commandSignatureOf(commandOf(toolInput)) : '';
}

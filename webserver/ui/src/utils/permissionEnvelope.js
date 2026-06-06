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
    requestId: String(raw?.request_id || raw?.id || nested.request_id || nested.id || ''),
    toolName: String(
      raw?.tool_name || raw?.tool
      || nested.tool_name || nested.tool || 'tool',
    ),
    toolInput: raw?.input || nested.input || {},
    outsideSandbox: !!(raw?.outside_sandbox || nested.outside_sandbox),
    outsidePath: String(raw?.outside_path || nested.outside_path || ''),
  };
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
const NOISE_PROGRAMS = new Set(['cd', 'pushd', 'popd', 'export', 'source', '.']);

// The program a single shell segment invokes, basename-only:
//   "JAVA_HOME=/x mvn -B verify" → "mvn"   "/usr/local/bin/docker ps" → "docker"
//   "./gradlew build"            → "gradlew"
function _programOfSegment(segment) {
  const tokens = String(segment).trim().split(/\s+/).filter(Boolean);
  let i = 0;
  // Skip leading env-var assignments (FOO=bar) — they prefix, not invoke.
  while (i < tokens.length && /^[A-Za-z_][A-Za-z0-9_]*=/.test(tokens[i])) { i += 1; }
  const prog = tokens[i];
  if (!prog) { return ''; }
  return prog.replace(/^.*\//, ''); // strip any path → basename (also kills ./)
}

// The remembered KEY for a command: the set of programs it actually runs,
// path/arg/cwd-independent, so the same `mvn verify` matches across task
// folders. ALL programs in a chain are included (deduped, in order) so that
// `mvn … && rm -rf …` ("mvn rm") never matches a remembered bare `mvn` — a
// new program tacked onto an allowed one re-prompts instead of riding through.
export function commandSignatureOf(command) {
  const normalized = String(command || '').replace(/\s+/g, ' ').trim();
  if (!normalized) { return ''; }
  const meaningful = [];
  const noise = [];
  for (const segment of normalized.split(/&&|\|\||[;|]/)) {
    const prog = _programOfSegment(segment);
    if (!prog) { continue; }
    const bucket = NOISE_PROGRAMS.has(prog) ? noise : meaningful;
    if (!bucket.includes(prog)) { bucket.push(prog); }
  }
  return (meaningful.length ? meaningful : noise).join(' ');
}

// The (tool, command-signature) pair to remember/recall for a request: the
// program signature for command-keyed tools, else '' (tool-level).
export function decisionCommandFor(toolName, toolInput) {
  return isCommandKeyedTool(toolName) ? commandSignatureOf(commandOf(toolInput)) : '';
}

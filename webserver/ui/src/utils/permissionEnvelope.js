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

// Tools that EXECUTE software (run arbitrary commands). These get the red,
// always-prompt treatment — kato must never silently run `docker`, build
// scripts, etc. on a remembered approval. Bash is the vector; Monitor runs
// a command in its wait loop too.
const EXECUTION_TOOLS = new Set(['Bash', 'Monitor']);

export function isExecutionTool(toolName) {
  return EXECUTION_TOOLS.has(String(toolName || ''));
}

// The command string an execution tool will run (for the warning), or ''.
export function executionCommand(toolInput) {
  if (!toolInput || typeof toolInput !== 'object') { return ''; }
  return String(toolInput.command || '').trim();
}

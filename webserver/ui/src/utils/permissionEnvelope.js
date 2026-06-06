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

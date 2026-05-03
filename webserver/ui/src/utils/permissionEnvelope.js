// ``control_request`` nests under `request`; older ``permission_request`` is flat.
export function unpackPermissionEnvelope(raw) {
  const nested = (raw && typeof raw.request === 'object' && raw.request) || {};
  return {
    requestId: String(raw?.request_id || raw?.id || nested.request_id || nested.id || ''),
    toolName: String(
      raw?.tool_name || raw?.tool
      || nested.tool_name || nested.tool || 'tool',
    ),
    toolInput: raw?.input || nested.input || {},
  };
}

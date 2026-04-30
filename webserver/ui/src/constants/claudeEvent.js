// Claude CLI stream-json event types — the wire protocol kato consumes
// from `claude -p --output-format stream-json` and replays from history.
//
// `PERMISSION_REQUEST` is the older shape; `CONTROL_REQUEST` is what
// `--permission-prompt-tool stdio` emits. Both surface as the same
// pendingPermission state in the reducer.
//
// `PERMISSION_RESPONSE` is kato-synthetic: the server appends it after a
// user answers a permission prompt so reconnecting browsers can clear
// stale modals from the backlog.

export const CLAUDE_EVENT = Object.freeze({
  ASSISTANT: 'assistant',
  USER: 'user',
  SYSTEM: 'system',
  RESULT: 'result',
  STREAM_EVENT: 'stream_event',
  PERMISSION_REQUEST: 'permission_request',
  CONTROL_REQUEST: 'control_request',
  PERMISSION_RESPONSE: 'permission_response',
});

export const CLAUDE_SYSTEM_SUBTYPE = Object.freeze({
  INIT: 'init',
});

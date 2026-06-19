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
  // Kato-synthetic: emitted from the workspace provisioner's
  // ``.kato-preflight.log`` so the operator sees ``cloning 1/3:
  // admin-client`` / ``✓ all repositories cloned — starting agent``
  // bubbles in the chat tab while kato is preparing the workspace.
  PREFLIGHT: 'preflight',
  // Kato-synthetic: the agent wrote outside the task folder without a
  // permission request (e.g. a /tmp scratch file the CLI auto-accepted).
  // Rendered as a loud warning bubble so it's never silent.
  SANDBOX_WARNING: 'sandbox_warning',
  // Kato-synthetic: the Action Guard BLOCKED a tool call. The agent was
  // refused and adapts; the operator sees a loud red bubble + tab signal.
  ACTION_GUARD_BLOCK: 'action_guard_block',
});

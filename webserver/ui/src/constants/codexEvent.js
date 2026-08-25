// Codex CLI wire-protocol event types (``codex exec --json`` JSONL).
//
// A different vocabulary from Claude's, not a dialect of it: Codex emits a
// thread/turn lifecycle plus one ``item.completed`` per produced artefact,
// where Claude emits assistant/result. The chat had no entry for any of it,
// so a working Codex turn rendered as four grey chips reading
// "thread.started / turn.started / item.completed / turn.completed" — the
// event NAMES — while the actual reply sat unread inside the item, and the
// working indicator span forever because nothing told it the turn had ended.
export const CODEX_EVENT = Object.freeze({
  THREAD_STARTED: 'thread.started',
  TURN_STARTED: 'turn.started',
  ITEM_STARTED: 'item.started',
  ITEM_UPDATED: 'item.updated',
  ITEM_COMPLETED: 'item.completed',
  TURN_COMPLETED: 'turn.completed',
  TURN_FAILED: 'turn.failed',
  TURN_ABORTED: 'turn.aborted',
  ERROR: 'error',
});

// ``item.completed`` carries an ``item`` whose own ``type`` says what it is.
export const CODEX_ITEM = Object.freeze({
  AGENT_MESSAGE: 'agent_message',
  REASONING: 'reasoning',
  COMMAND_EXECUTION: 'command_execution',
  FILE_CHANGE: 'file_change',
  MCP_TOOL_CALL: 'mcp_tool_call',
  WEB_SEARCH: 'web_search',
  TODO_LIST: 'todo_list',
  ERROR: 'error',
});

// Turn-lifecycle events that END a turn — the UI's "working" indicator
// clears on any of them.
export const CODEX_TERMINAL_EVENTS = Object.freeze([
  CODEX_EVENT.TURN_COMPLETED,
  CODEX_EVENT.TURN_FAILED,
  CODEX_EVENT.TURN_ABORTED,
]);

// Pure lifecycle noise: they mark a turn starting and carry nothing the
// operator needs to read.
export const CODEX_HIDDEN_EVENTS = Object.freeze([
  CODEX_EVENT.THREAD_STARTED,
  CODEX_EVENT.TURN_STARTED,
  CODEX_EVENT.ITEM_STARTED,
  CODEX_EVENT.ITEM_UPDATED,
]);

export function isCodexTerminal(type) {
  return CODEX_TERMINAL_EVENTS.includes(String(type || ''));
}

export function isCodexHidden(type) {
  return CODEX_HIDDEN_EVENTS.includes(String(type || ''));
}

// The assistant's prose from an ``item.completed``, or '' when the item is
// something else (a command run, a reasoning trace, a file edit).
export function codexAgentMessage(raw) {
  if (String(raw?.type || '') !== CODEX_EVENT.ITEM_COMPLETED) { return ''; }
  const item = raw.item || {};
  if (String(item.type || '') !== CODEX_ITEM.AGENT_MESSAGE) { return ''; }
  return String(item.text || '').trim();
}

// Translates a Claude tool_use block into a short, human-readable summary
// instead of the raw `Bash({"command": "..."})` JSON dump. The goal is the
// user can glance at the event log and immediately see what kato is doing,
// not parse JSON.

import { stringifyShort } from './dom.js';

const FORMATTERS = {
  Bash: (input) => {
    const cmd = String(input?.command || '').trim();
    if (!cmd) { return '$ (empty)'; }
    return `$ ${truncate(cmd, 120)}`;
  },
  Read: (input) => {
    const p = String(input?.file_path || '');
    return `Read · ${shortPath(p)}`;
  },
  Edit: (input) => {
    const p = String(input?.file_path || '');
    return `Edit · ${shortPath(p)}`;
  },
  MultiEdit: (input) => {
    const p = String(input?.file_path || '');
    const n = Array.isArray(input?.edits) ? input.edits.length : 0;
    const editLabel = n === 1 ? '1 edit' : `${n} edits`;
    return `Edit · ${shortPath(p)} (${editLabel})`;
  },
  Write: (input) => {
    const p = String(input?.file_path || '');
    return `Write · ${shortPath(p)}`;
  },
  NotebookEdit: (input) => {
    const p = String(input?.notebook_path || '');
    return `Notebook · ${shortPath(p)}`;
  },
  Glob: (input) => {
    const pattern = String(input?.pattern || '');
    const path = String(input?.path || '');
    if (path) { return `Glob · ${pattern} in ${shortPath(path)}`; }
    return `Glob · ${pattern}`;
  },
  Grep: (input) => {
    const pattern = String(input?.pattern || '');
    const path = String(input?.path || '');
    if (path) { return `Grep · "${pattern}" in ${shortPath(path)}`; }
    return `Grep · "${pattern}"`;
  },
  WebFetch: (input) => {
    const url = String(input?.url || '');
    try {
      const host = new URL(url).host;
      return `WebFetch · ${host}`;
    } catch (_) {
      return `WebFetch · ${truncate(url, 80)}`;
    }
  },
  WebSearch: (input) => {
    const query = String(input?.query || '');
    return `WebSearch · "${truncate(query, 80)}"`;
  },
  Agent: (input) => {
    const subagent = String(input?.subagent_type || 'agent');
    const desc = String(input?.description || '');
    if (desc) { return `Agent (${subagent}) · ${truncate(desc, 80)}`; }
    return `Agent · ${subagent}`;
  },
  TodoWrite: (input) => {
    const todos = Array.isArray(input?.todos) ? input.todos : [];
    return `TodoWrite · ${todos.length} item${todos.length === 1 ? '' : 's'}`;
  },
  KillShell: (input) => {
    const id = String(input?.shell_id || input?.task_id || '');
    return `KillShell${id ? ` · ${id}` : ''}`;
  },
  TaskOutput: (input) => {
    const id = String(input?.task_id || '');
    return `TaskOutput${id ? ` · ${id}` : ''}`;
  },
};

export function formatToolUse(toolName, input) {
  const formatter = FORMATTERS[toolName];
  if (formatter) {
    try {
      return formatter(input || {});
    } catch (err) {
      // Surface formatter bugs in dev tools rather than silently
      // hiding them behind the raw fallback.
      if (typeof console !== 'undefined' && console.warn) {
        console.warn(`formatToolUse(${toolName}) threw:`, err);
      }
    }
  }
  // Unknown tool — fall back to the legacy compact-JSON rendering.
  return `${toolName}(${stringifyShort(input)})`;
}

function shortPath(path) {
  if (!path) { return ''; }
  const parts = path.split(/[\\/]/).filter(Boolean);
  if (parts.length <= 2) { return path; }
  // Keep last 2 segments. Only return the elided form when the elided
  // prefix is actually longer than the "…/" we'd add, otherwise the
  // "shortened" path is longer than the original.
  const tail = parts.slice(-2).join('/');
  const shortened = `…/${tail}`;
  return shortened.length < path.length ? shortened : path;
}

function truncate(text, max) {
  if (!text || text.length <= max) { return text; }
  return text.slice(0, max - 1) + '…';
}

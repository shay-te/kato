// Translates a Claude tool_use block into a human-readable rendering
// for the chat event log.
//
// Returns either:
//   - a plain string (the legacy header-only form), OR
//   - ``{ summary, details }`` where ``details`` is a multi-line
//     code/diff block to render under the header.
//
// The bubble renderer (EventLog) accepts both shapes.
//
// Design intent: full transparency. The operator sees the exact path
// the agent touched, the exact command it ran, and — for edits — the
// before/after snippet inline (the same surface Claude Code's CLI
// shows in its own UI). No path elision, no command truncation.

import { stringifyShort } from './dom.js';


const FORMATTERS = {
  Bash: (input) => {
    const cmd = String(input?.command || '').trim();
    if (!cmd) { return '$ (empty)'; }
    // Multi-line commands (heredocs, scripts) render as a code
    // block under the header so the operator sees every line.
    const lines = cmd.split('\n');
    if (lines.length > 1) {
      return {
        summary: `$ ${lines[0]}`,
        details: lines.slice(1).join('\n'),
      };
    }
    return `$ ${cmd}`;
  },
  Read: (input) => `Read · ${String(input?.file_path || '')}`,
  Edit: (input) => {
    const path = String(input?.file_path || '');
    const oldStr = String(input?.old_string || '');
    const newStr = String(input?.new_string || '');
    return {
      summary: `Edit · ${path}`,
      details: formatEditDiff(oldStr, newStr),
    };
  },
  MultiEdit: (input) => {
    const path = String(input?.file_path || '');
    const edits = Array.isArray(input?.edits) ? input.edits : [];
    const editLabel = edits.length === 1 ? '1 edit' : `${edits.length} edits`;
    if (edits.length === 0) {
      return `Edit · ${path} (${editLabel})`;
    }
    const blocks = edits.map((edit) => {
      const oldStr = String(edit?.old_string || '');
      const newStr = String(edit?.new_string || '');
      return formatEditDiff(oldStr, newStr);
    });
    return {
      summary: `Edit · ${path} (${editLabel})`,
      details: blocks.join('\n---\n'),
    };
  },
  Write: (input) => {
    const path = String(input?.file_path || '');
    const content = String(input?.content || '');
    if (!content) { return `Write · ${path}`; }
    return {
      summary: `Write · ${path}`,
      details: prefixLines(content, '+ '),
    };
  },
  NotebookEdit: (input) => {
    const path = String(input?.notebook_path || '');
    return `Notebook · ${path}`;
  },
  Glob: (input) => {
    const pattern = String(input?.pattern || '');
    const path = String(input?.path || '');
    if (path) { return `Glob · ${pattern} in ${path}`; }
    return `Glob · ${pattern}`;
  },
  Grep: (input) => {
    const pattern = String(input?.pattern || '');
    const path = String(input?.path || '');
    if (path) { return `Grep · "${pattern}" in ${path}`; }
    return `Grep · "${pattern}"`;
  },
  WebFetch: (input) => `WebFetch · ${String(input?.url || '')}`,
  WebSearch: (input) => `WebSearch · "${String(input?.query || '')}"`,
  Agent: (input) => {
    const subagent = String(input?.subagent_type || 'agent');
    const desc = String(input?.description || '');
    if (desc) { return `Agent (${subagent}) · ${desc}`; }
    return `Agent · ${subagent}`;
  },
  TodoWrite: (input) => {
    const todos = Array.isArray(input?.todos) ? input.todos : [];
    if (todos.length === 0) {
      return 'TodoWrite · 0 items';
    }
    // Show every todo with its status — operators want to see the
    // plan the agent is tracking.
    const lines = todos.map((todo) => {
      const status = String(todo?.status || 'pending');
      const content = String(todo?.content || todo?.activeForm || '');
      const marker = _statusMarker(status);
      return `${marker} ${content}`;
    });
    return {
      summary: `TodoWrite · ${todos.length} item${todos.length === 1 ? '' : 's'}`,
      details: lines.join('\n'),
    };
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
      if (typeof console !== 'undefined' && console.warn) {
        console.warn(`formatToolUse(${toolName}) threw:`, err);
      }
    }
  }
  // Unknown tool — fall back to legacy compact-JSON rendering.
  return `${toolName}(${stringifyShort(input)})`;
}


// Render an old → new edit as a unified-diff-style block:
//   - old line
//   + new line
// Each of old_string and new_string can be multi-line; we tag every
// line so the operator can scan the change visually.
function formatEditDiff(oldStr, newStr) {
  const oldBlock = prefixLines(oldStr, '- ');
  const newBlock = prefixLines(newStr, '+ ');
  if (!oldBlock && !newBlock) { return ''; }
  if (!oldBlock) { return newBlock; }
  if (!newBlock) { return oldBlock; }
  return `${oldBlock}\n${newBlock}`;
}


function prefixLines(text, prefix) {
  const raw = String(text || '');
  if (!raw) { return ''; }
  const lines = raw.split('\n');
  // Drop a single trailing blank line (string ending in \n) so we
  // don't emit a stray prefix-only row.
  if (lines.length > 1 && lines[lines.length - 1] === '') {
    lines.pop();
  }
  return lines.map((line) => `${prefix}${line}`).join('\n');
}


function _statusMarker(status) {
  switch (status) {
    case 'completed': return '✓';
    case 'in_progress': return '→';
    case 'cancelled': return '✗';
    default: return '·';
  }
}

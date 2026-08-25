// No chat surface may hardcode an agent's name.
//
// This has now been reported five separate times — the composer placeholder,
// the spawn message, the new-chat bubble, the status chip, and the assistant
// bubble label — each time as "why does the Codex tab say Claude?". They all
// predate agent tabs, when there was only ever one agent to name. This test
// exists so the sixth one fails here instead of in front of the operator.
//
// Scope is deliberately the CHAT surface: files that render the running
// conversation. Genuinely Claude-specific UI (its permission model, adopting
// a Claude Code session) names Claude correctly and is not covered.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const DIR = new URL('.', import.meta.url).pathname;

// Files whose text the operator reads while chatting with EITHER agent.
const CHAT_SURFACE = [
  'Bubble.jsx',
  'EventLog.jsx',
  'MessageForm.jsx',
  'SessionDetail.jsx',
  'SessionHeader.jsx',
  'AgentBackendTabs.jsx',
];

// A user-visible string: a quoted literal or a JSX text node. Comments are
// stripped first — they explain the bug and necessarily mention Claude.
function visibleStrings(source) {
  const withoutComments = source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');
  const out = [];
  // Quoted literals and template literals.
  for (const m of withoutComments.matchAll(/(['"`])((?:\\.|(?!\1)[^\\])*)\1/g)) {
    out.push(m[2]);
  }
  return out;
}

// Names that must never be baked into a chat string.
const AGENT_NAMES = /\b(Claude|Codex)\b/;

// Strings that legitimately contain a name.
const ALLOWED = [
  // The label map that DEFINES the names.
  /^claude$/i, /^codex$/i,
  // Import paths and class/DOM names.
  /AgentBackendChip|claudeEvent|codexEvent|ClaudePermissions|claude-status/,
  // Claude Code the PRODUCT, not the agent in this chat.
  /Claude Code/,
  // The adopt-session control is Claude-specific by nature.
  /Adopt .*Claude|Claude session for this task/,
];

test('no chat surface hardcodes an agent name in visible text', () => {
  const offenders = [];
  for (const file of CHAT_SURFACE) {
    const source = readFileSync(join(DIR, file), 'utf8');
    for (const text of visibleStrings(source)) {
      if (!AGENT_NAMES.test(text)) { continue; }
      if (ALLOWED.some((ok) => ok.test(text))) { continue; }
      offenders.push(`${file}: ${JSON.stringify(text.slice(0, 80))}`);
    }
  }
  assert.deepEqual(
    offenders, [],
    'these name an agent in text the OTHER agent\'s tab also shows — derive '
    + 'the name from the active backend (backendLabel) instead:\n  '
    + offenders.join('\n  '),
  );
});

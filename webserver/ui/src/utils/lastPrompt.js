import { BUBBLE_KIND } from '../constants/bubbleKind.js';
import { CLAUDE_EVENT } from '../constants/claudeEvent.js';
import { ENTRY_SOURCE } from '../constants/entrySource.js';

// The most recent operator prompt, plain text. Drives the sticky
// "you asked:" bar pinned at the top of the chat (like the Claude
// VS Code plugin) so the operator never loses sight of the request
// the agent is currently working on, however far the log scrolls.
//
// Scans newest→oldest and returns the first USER message it finds:
//   * a locally-echoed send (ENTRY_SOURCE.LOCAL, kind USER), or
//   * a server ``user`` event (text blocks in raw.message.content).
// Returns '' when there's no user message yet.
export function lastUserPromptText(entries) {
  if (!Array.isArray(entries)) { return ''; }
  for (let i = entries.length - 1; i >= 0; i -= 1) {
    const entry = entries[i];
    if (!entry) { continue; }
    if (entry.source === ENTRY_SOURCE.LOCAL) {
      if (entry.kind === BUBBLE_KIND.USER) {
        const text = String(entry.text || '').trim();
        if (text) { return text; }
      }
      continue;
    }
    const raw = entry.raw;
    if (raw && raw.type === CLAUDE_EVENT.USER) {
      const content = Array.isArray(raw.message?.content)
        ? raw.message.content : [];
      const text = content
        .filter((b) => b && b.type === 'text' && b.text)
        .map((b) => b.text)
        .join('\n')
        .trim();
      if (text) { return text; }
    }
  }
  return '';
}

// Recognise the API's "safeguards flagged this session" refusal in the chat,
// so the UI can offer a one-click retry on a lower model version.
//
// Mirrors claude_core_lib/helpers/safeguard_error.py — the message format is
// Anthropic's, and the two must stay in step (a shared pinning test is not
// possible across the language boundary, so keep the anchors identical). The
// anchors are the stable parts: the AUP link and "flagged this session". The
// model NAME in the text drifts and is only read for display.

import { CLAUDE_EVENT } from '../constants/claudeEvent.js';
import { ENTRY_SOURCE } from '../constants/entrySource.js';
import { BUBBLE_KIND } from '../constants/bubbleKind.js';

const AUP_LINK = /anthropic\.com\/legal\/aup/i;
const FLAGGED = /safeguards?\s+flagged\s+th(?:is|e)\s+session/i;
const REQUEST_ID = /Request ID:\s*(req_[A-Za-z0-9]+)/i;
const DETAILS = /Details:\s*\[([^\]]+)\]/i;
const MODEL = /([A-Z][A-Za-z0-9.\- ]*?)'s safeguards/i;

export function isSafeguardFlag(text) {
  const value = String(text || '');
  return AUP_LINK.test(value) && FLAGGED.test(value);
}

// ``{ details, requestId, model }`` when ``text`` is a safeguards refusal,
// else null.
export function parseSafeguardFlag(text) {
  const value = String(text || '');
  if (!isSafeguardFlag(value)) { return null; }
  const requestId = REQUEST_ID.exec(value);
  const details = DETAILS.exec(value);
  const model = MODEL.exec(value);
  return {
    details: details ? details[1].trim() : '',
    requestId: requestId ? requestId[1] : '',
    model: model ? model[1].trim() : '',
  };
}

// A chat entry that is a fresh user prompt — a newer one AFTER a flagged
// result means the operator has already moved on, so the offer is stale.
function isNewerPrompt(entry) {
  if (!entry) { return false; }
  if (entry.source === ENTRY_SOURCE.LOCAL) {
    return (entry.kind || BUBBLE_KIND.SYSTEM) === BUBBLE_KIND.USER;
  }
  return entry.raw?.type === CLAUDE_EVENT.USER;
}

// Scan the chat entries newest-first: the loop is "currently flagged" only
// when the MOST RECENT turn result is a safeguards refusal and no newer prompt
// has been sent since. Returns the parsed flag, or null.
export function findSafeguardFlag(entries) {
  const list = Array.isArray(entries) ? entries : [];
  for (let i = list.length - 1; i >= 0; i -= 1) {
    const entry = list[i];
    const raw = entry?.raw;
    if (raw && raw.type === CLAUDE_EVENT.RESULT) {
      return raw.is_error ? parseSafeguardFlag(raw.result || '') : null;
    }
    if (isNewerPrompt(entry)) { return null; }
  }
  return null;
}

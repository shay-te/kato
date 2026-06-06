// Single source of truth for the operator's remembered tool-permission
// decisions ("Allow always" / "Deny always"), keyed by tool name and
// persisted to localStorage so they survive a kato + browser restart.
//
// Why a shared store (not a per-hook useState): the permission PROMPT
// (App's useToolMemory) and the SETTINGS panel that lists + clears these
// decisions both read AND write the same set. If each kept its own
// in-memory copy, clearing "Edit=allow" in settings would leave App's
// copy stale and it would keep auto-allowing the very tool the operator
// just revoked — a real hole for a security control. One module-level
// map + pub/sub (same shape as toastStore / agentStatusStore) means every
// surface mutates and observes the exact same value.

import { readStorageString, writeStorageItem } from '../utils/storage.js';
import { parseJsonOr } from '../utils/json.js';
import { createPubSub } from './pubsub.js';

export const TOOL_DECISIONS_STORAGE_KEY = 'kato.toolDecisions.v1';

// Exported (also re-exported by useToolMemory for back-compat) so the
// persistence layer can be unit-tested without React.
export function readPersisted() {
  const parsed = parseJsonOr(readStorageString(TOOL_DECISIONS_STORAGE_KEY, null), null);
  if (!parsed || typeof parsed !== 'object') { return {}; }
  return parsed;
}

export function writePersisted(decisions) {
  writeStorageItem(TOOL_DECISIONS_STORAGE_KEY, JSON.stringify(decisions), undefined);
}

let _decisions = readPersisted();
const _pubsub = createPubSub(() => _decisions);

function _commit(next) {
  _decisions = next;
  writePersisted(next);
  _pubsub.emit();
}

export const toolDecisionsStore = {
  subscribe: _pubsub.subscribe,

  get() { return _decisions; },

  recall(toolName) {
    if (!toolName) { return null; }
    return _decisions[toolName] || null;
  },

  // Stable, name-sorted list for the settings panel.
  entries() {
    return Object.keys(_decisions)
      .map((tool) => ({ tool, decision: _decisions[tool] }))
      .sort((a, b) => a.tool.localeCompare(b.tool));
  },

  // Set an explicit scope ('allow' | 'deny'). Anything not 'deny' is
  // coerced to 'allow' (mirrors the old ``allow ? 'allow' : 'deny'``).
  // No-op + no emit when unchanged, so a settings re-select of the
  // current value can't cascade a render loop.
  setDecision(toolName, decision) {
    if (!toolName) { return; }
    const value = decision === 'deny' ? 'deny' : 'allow';
    if (_decisions[toolName] === value) { return; }
    _commit({ ..._decisions, [toolName]: value });
  },

  remember(toolName, allow) {
    this.setDecision(toolName, allow ? 'allow' : 'deny');
  },

  // forget(tool) drops one; forget() (no arg) clears all. Emits only
  // when something actually changed.
  forget(toolName) {
    if (!toolName) {
      if (Object.keys(_decisions).length === 0) { return; }
      _commit({});
      return;
    }
    if (!(toolName in _decisions)) { return; }
    const next = { ..._decisions };
    delete next[toolName];
    _commit(next);
  },

  // Cross-tab sync: another browser tab persisted a change; re-read the
  // canonical localStorage value and notify local subscribers.
  syncFromStorage() {
    _decisions = readPersisted();
    _pubsub.emit();
  },
};

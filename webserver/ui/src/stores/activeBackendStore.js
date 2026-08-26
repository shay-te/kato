// Single source of truth for WHICH AGENT TAB the operator is on, keyed by task.
//
// Every consumer used to read ``session.agent_backend`` — a field on the
// 5s-polled /api/sessions record. That is where the record BELIEVES the chat
// is, which lags the tab strip by up to a poll interval and, right after a
// switch, is simply the previous value. Three separate bugs came out of that
// one substitution:
//
//   * a message typed in the Codex tab was sent tagged ``claude``, and the
//     server's "the tab is authoritative" rule then re-pointed the record —
//     so the chat visibly moved to Claude on the next refresh;
//   * the CLI upgrade button on the Codex banner ran Claude's upgrade;
//   * agent-named copy said the wrong name for a beat after every switch.
//
// ``AgentBackendTabs`` owns the answer (it holds the optimistic pick as well
// as the record's value) and is the ONLY writer. Everything else subscribes.
// Plain pub/sub, like agentStatusStore — no React, no context.

import { useEffect, useState } from 'react';
import { createPubSub } from './pubsub.js';

// { [taskId]: backendId }
let _active = {};

const _pubsub = createPubSub(() => _active);
const _emit = _pubsub.emit;

export const activeBackendStore = {
  subscribe: _pubsub.subscribe,

  // Called by AgentBackendTabs whenever the selected tab resolves.
  set(taskId, backend) {
    if (!taskId) { return; }
    const value = String(backend || '').trim().toLowerCase();
    if (_active[taskId] === value) { return; }
    _active = { ..._active, [taskId]: value };
    _emit();
  },

  get(taskId) {
    return (taskId && _active[taskId]) || '';
  },

  // A task the operator forgot / closed should not pin a stale answer.
  clear(taskId) {
    if (!taskId || !(taskId in _active)) { return; }
    const next = { ..._active };
    delete next[taskId];
    _active = next;
    _emit();
  },

  clearAll() {
    if (Object.keys(_active).length === 0) { return; }
    _active = {};
    _emit();
  },
};

// The selected tab for ``taskId``, falling back to ``recordBackend`` (what the
// session record says) until the tab strip has reported in — which is the
// correct answer on first paint, before any switch has happened.
export function useActiveBackend(taskId, recordBackend = '') {
  const [value, setValue] = useState(() => activeBackendStore.get(taskId));
  useEffect(() => {
    setValue(activeBackendStore.get(taskId));
    return activeBackendStore.subscribe(() => {
      setValue(activeBackendStore.get(taskId));
    });
  }, [taskId]);
  return value || String(recordBackend || '').trim().toLowerCase();
}

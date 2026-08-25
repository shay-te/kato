import React, { useEffect, useState } from 'react';
import ChatsMenu from './ChatsMenu.jsx';
import { backendLabel } from './AgentBackendChip.jsx';
import { fetchAgentBackends, switchTaskBackend } from '../api.js';
import { normalizeBackendEntries, normalizeBackendEntry }
  from '../utils/agentBackendEntry.js';
import { toast } from '../stores/toastStore.js';

// One tab per agent this host can run, each owning its own chat history —
// the same shape the editor extensions use.
//
// The tabs are separate CONVERSATIONS, not a display filter: asking Codex
// something must not replace the Claude thread the operator was in the
// middle of. Switching parks the outgoing chat and lifts the incoming one,
// so coming back finds it exactly where it was. The per-tab history button
// therefore lists only that backend's chats — offering a Claude tab a Codex
// thread would offer a conversation it cannot resume.
//
// With a single backend wired there is nothing to switch between, so the
// tab strip collapses to just that backend's history button.
export default function AgentBackendTabs({
  taskId,
  activeBackend = '',
  onBackendChanged,
  onChatChanged,
  onChatSwitchPending,
  onReadinessChange,
  turnInFlight = false,
}) {
  const [backends, setBackends] = useState([]);
  const [switching, setSwitching] = useState(false);

  useEffect(() => {
    let cancelled = false;
    // Best-effort: a failed lookup leaves one tab rather than blocking chat.
    fetchAgentBackends()
      .then((body) => {
        if (cancelled) { return; }
        setBackends(normalizeBackendEntries(body?.backends));
      })
      .catch(() => { if (!cancelled) { setBackends([]); } });
    return () => { cancelled = true; };
  }, []);

  // Until the list loads, show the tab the session is actually on rather
  // than nothing — the history button must not disappear on every remount.
  // A pre-load placeholder is assumed READY: the session is already running
  // on it, so flashing a setup panel over a working chat would be a lie.
  const tabs = backends.length > 0
    ? backends
    : (activeBackend ? [normalizeBackendEntry(activeBackend)] : []);
  const current = activeBackend || tabs[0]?.id || '';
  const currentEntry = tabs.find((t) => t.id === current) || null;

  // The chat area needs to know whether to render a chat or a setup panel,
  // and only this component knows what the probe said.
  useEffect(() => {
    if (typeof onReadinessChange === 'function') {
      onReadinessChange(currentEntry);
    }
  }, [onReadinessChange, currentEntry]);

  async function pickBackend(backend) {
    if (switching || backend === current) { return; }
    setSwitching(true);
    try {
      const result = await switchTaskBackend(taskId, backend);
      if (!result.ok) {
        toast.errorFromResult(result, {
          title: 'Could not switch agent', fallback: 'unknown error',
        });
        return;
      }
      if (typeof onBackendChanged === 'function') {
        onBackendChanged(backend, result.body || {});
      }
    } finally {
      setSwitching(false);
    }
  }

  if (tabs.length === 0) { return null; }

  return (
    <div className="agent-backend-tabs" role="tablist"
         aria-label="Agent for this task">
      {tabs.map((entry) => {
        const backend = entry.id;
        const active = backend === current;
        // Not ready = its CLI is missing or won't answer. The tab still
        // shows (hiding it is how the operator never learns the backend
        // exists) — selecting it opens setup instructions instead of a chat.
        const unready = entry.ready === false;
        return (
          <div
            key={backend}
            className={[
              'agent-backend-tab',
              active ? 'is-active' : '',
              unready ? 'is-unready' : '',
            ].filter(Boolean).join(' ')}
          >
            <button
              type="button"
              role="tab"
              aria-selected={active}
              className="agent-backend-tab-button"
              onClick={() => pickBackend(backend)}
              disabled={switching}
              title={unready
                ? `${backendLabel(backend)} is not set up on this host — `
                  + 'open the tab for instructions'
                : active
                  ? `This task's chat is running on ${backendLabel(backend)}`
                  : `Switch to ${backendLabel(backend)} — the current `
                    + 'conversation is kept and can be switched back to'}
            >
              {backendLabel(backend)}
              {unready && (
                <span className="agent-backend-tab-unready" aria-hidden="true">
                  !
                </span>
              )}
            </button>
            {/* Each tab carries its OWN history. Rendered only for the
                active tab: the menu acts on the task's live chat, and an
                inactive tab's button would switch conversations behind the
                operator's back. */}
            {active && !unready ? (
              <ChatsMenu
                taskId={taskId}
                agentBackend={backend}
                onChatChanged={onChatChanged}
                onChatSwitchPending={onChatSwitchPending}
                turnInFlight={turnInFlight}
              />
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

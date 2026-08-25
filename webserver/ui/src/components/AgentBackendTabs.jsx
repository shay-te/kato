import React, { useEffect, useState } from 'react';
import ChatsMenu from './ChatsMenu.jsx';
import { backendLabel } from './AgentBackendChip.jsx';
import { fetchAgentBackends, switchTaskBackend } from '../api.js';
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
        setBackends(Array.isArray(body?.backends) ? body.backends : []);
      })
      .catch(() => { if (!cancelled) { setBackends([]); } });
    return () => { cancelled = true; };
  }, []);

  // Until the list loads, show the tab the session is actually on rather
  // than nothing — the history button must not disappear on every remount.
  const tabs = backends.length > 0
    ? backends
    : (activeBackend ? [activeBackend] : []);
  const current = activeBackend || tabs[0] || '';

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
      {tabs.map((backend) => {
        const active = backend === current;
        return (
          <div
            key={backend}
            className={`agent-backend-tab${active ? ' is-active' : ''}`}
          >
            <button
              type="button"
              role="tab"
              aria-selected={active}
              className="agent-backend-tab-button"
              onClick={() => pickBackend(backend)}
              disabled={switching}
              title={active
                ? `This task's chat is running on ${backendLabel(backend)}`
                : `Switch to ${backendLabel(backend)} — the current `
                  + 'conversation is kept and can be switched back to'}
            >
              {backendLabel(backend)}
            </button>
            {/* Each tab carries its OWN history. Rendered only for the
                active tab: the menu acts on the task's live chat, and an
                inactive tab's button would switch conversations behind the
                operator's back. */}
            {active ? (
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

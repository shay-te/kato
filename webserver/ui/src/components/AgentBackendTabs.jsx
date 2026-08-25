import React, { useEffect, useState } from 'react';
import ChatsMenu from './ChatsMenu.jsx';
import { backendLabel } from './AgentBackendChip.jsx';
import { fetchAgentBackends, switchTaskBackend } from '../api.js';
import { normalizeBackendEntries, normalizeBackendEntry }
  from '../utils/agentBackendEntry.js';
import { useActiveChatTitle } from '../hooks/useActiveChatTitle.js';
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
  // The backend the operator just PICKED, held locally until the session
  // prop catches up.
  //
  // Selection used to be derived from ``activeBackend`` alone — which comes
  // from the session record App polls. The switch POST updates that record
  // on the SERVER, but nothing in this render tree re-reads it, so a
  // successful switch changed nothing on screen and the tab snapped
  // straight back to Claude. From the operator's side the click simply did
  // not work.
  const [pickedBackend, setPickedBackend] = useState('');

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
  const current = pickedBackend || activeBackend || tabs[0]?.id || '';
  const currentEntry = tabs.find((t) => t.id === current) || null;

  // Bumped after any chat mutation so the title bar re-reads.
  const [chatNonce, setChatNonce] = useState(0);
  const chatName = useActiveChatTitle(taskId, current, chatNonce);

  function handleChatChanged(result) {
    setChatNonce((n) => n + 1);
    if (typeof onChatChanged === 'function') { onChatChanged(result); }
  }

  // Hand selection back to the session once it agrees — from then on the
  // server is the source of truth again, and a switch made elsewhere (or a
  // task the operator re-opens) is reflected instead of being pinned by a
  // stale local pick.
  useEffect(() => {
    if (pickedBackend && activeBackend === pickedBackend) {
      setPickedBackend('');
    }
  }, [activeBackend, pickedBackend]);

  // The chat area needs to know whether to render a chat or a setup panel,
  // and only this component knows what the probe said.
  useEffect(() => {
    if (typeof onReadinessChange === 'function') {
      onReadinessChange(currentEntry);
    }
  }, [onReadinessChange, currentEntry]);

  async function pickBackend(backend) {
    if (switching || backend === current) { return; }
    // A backend that cannot chat is selected LOCALLY and nothing is posted:
    // the switch route refuses a backend it has no manager for, so asking
    // would answer a click on the setup tab with "Could not switch agent"
    // — an error about the very thing the tab exists to explain.
    const target = tabs.find((t) => t.id === backend);
    if (target && target.chat_available === false) {
      setPickedBackend(backend);
      return;
    }
    setSwitching(true);
    try {
      const result = await switchTaskBackend(taskId, backend);
      if (!result.ok) {
        // Left un-picked on purpose: the tab visibly stays where it was, so
        // a refused switch reads as "that didn't happen" rather than
        // selecting a tab whose first message would fail.
        toast.errorFromResult(result, {
          title: 'Could not switch agent', fallback: 'unknown error',
        });
        return;
      }
      // Trust the server's answer over the argument — a backend alias
      // resolves server-side, so the record may name it differently.
      setPickedBackend(String(result.body?.agent_backend || backend));
      if (typeof onBackendChanged === 'function') {
        onBackendChanged(backend, result.body || {});
      }
    } finally {
      setSwitching(false);
    }
  }

  if (tabs.length === 0) { return null; }

  const showChatBar = !!currentEntry && currentEntry.chat_available !== false;

  return (
    <div className="agent-backend-header">
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
          </div>
        );
      })}
    </div>
    {/* Second row: the conversation you are IN, and the controls that act on
        it. The chats control used to sit inside the active tab pill, where it
        read as part of the agent's name and the chat's own title appeared
        nowhere at all — you had to open the dropdown to find out which
        conversation you were looking at. */}
    {showChatBar && (
      <div className="agent-chat-bar">
        <span className="agent-chat-bar-title" title={chatName || 'Chats'}>
          {chatName || 'Chats'}
        </span>
        <ChatsMenu
          taskId={taskId}
          agentBackend={current}
          onChatChanged={handleChatChanged}
          onChatSwitchPending={onChatSwitchPending}
          turnInFlight={turnInFlight}
        />
      </div>
    )}
    </div>
  );
}

import { useEffect, useState } from 'react';
import AgentBackendChip, { backendLabel } from './AgentBackendChip.jsx';
import { fetchTaskChats, startTaskChat } from '../api.js';
import { AGENT_SESSION_ID } from '../constants/sessionFields.js';
import { useEscapeKey } from '../hooks/useEscapeKey.js';
import { toast } from '../stores/toastStore.js';
import { chatMeta, chatTitle } from './ChatsMenuHelpers.js';
import Icon from './Icon.jsx';

// Header dropdown for navigating a task's chats. "New chat" detaches the
// current conversation (the next message spawns a fresh Claude session);
// picking a previous chat resumes that conversation instead. The detached
// chat is never lost — it stays in the list and can be returned to.
export default function ChatsMenu({
  // Which backend's chats this menu shows and starts. Empty means "this
  // task's current chat", which is what a record predating per-backend
  // chats holds.
  agentBackend = '',
  taskId,
  onChatChanged,
  onChatSwitchPending = null,
  turnInFlight = false,
}) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState({ status: 'idle', chats: [], error: '' });
  const [busy, setBusy] = useState(false);
  // Mid-turn guard: switching chats KILLS the live subprocess. When Claude
  // is mid-turn, the first click arms this with the requested target and
  // shows a warning; only a second click on the same target proceeds.
  const [confirmTarget, setConfirmTarget] = useState(null);

  function close() {
    setOpen(false);
    setConfirmTarget(null);
  }
  useEscapeKey(close, open);

  // The warning's premise dies with the turn: once Claude is no longer
  // mid-turn, the armed confirm would show a false "Claude is mid-turn"
  // state (and the next click would act without the kill it warns about).
  useEffect(() => {
    if (!turnInFlight) { setConfirmTarget(null); }
  }, [turnInFlight]);

  async function loadChats() {
    setState({ status: 'loading', chats: [], error: '' });
    try {
      const body = await fetchTaskChats(taskId, agentBackend);
      setState({
        status: 'ready',
        chats: Array.isArray(body?.chats) ? body.chats : [],
        error: '',
      });
    } catch (err) {
      setState({ status: 'error', chats: [], error: String(err.message || err) });
    }
  }

  function toggle() {
    const next = !open;
    setOpen(next);
    setConfirmTarget(null);
    if (next) {
      loadChats();
    }
  }

  function notifySwitchPending(pending) {
    if (typeof onChatSwitchPending === 'function') {
      onChatSwitchPending(pending);
    }
  }

  async function runChatAction(agentSessionId, successToast, agentBackend = '') {
    if (busy) { return; }
    if (turnInFlight && confirmTarget !== agentSessionId) {
      setConfirmTarget(agentSessionId);
      return;
    }
    setBusy(true);
    // Armed BEFORE the request: the backend kill flips the stream's
    // turn-in-flight state (possibly before the POST resolves), and the
    // parent must not mistake that for a turn end (queued-message flush).
    notifySwitchPending(true);
    let succeeded = false;
    try {
      const result = await startTaskChat(taskId, agentSessionId, agentBackend);
      if (!result.ok) {
        toast.errorFromResult(result, {
          title: 'Chat action failed', fallback: 'unknown error',
        });
        return;
      }
      succeeded = true;
      close();
      toast.show(successToast);
      if (typeof onChatChanged === 'function') {
        onChatChanged(result.body || {});
      }
    } finally {
      setBusy(false);
      if (!succeeded) {
        // Failure / early return: the old chat is untouched, so re-enable
        // the normal queued-message flush and disarm the mid-turn confirm
        // (a stale warning would mislead the next interaction). On
        // success onChatChanged already cleared the pending flag while
        // discarding the stale queue, and close() disarmed the confirm.
        notifySwitchPending(false);
        setConfirmTarget(null);
      }
    }
  }

  function onNewChat(agentBackend = '') {
    const label = backendLabel(agentBackend);
    runChatAction('', {
      kind: 'success',
      title: label ? `New ${label} chat started` : 'New chat started',
      message: 'The previous conversation is kept in the chats menu — '
        + `your next message starts a fresh ${label || 'agent'} session.`,
    }, agentBackend);
  }

  function onPickChat(chat) {
    if (chat.active) {
      close();
      return;
    }
    const sid = String(chat[AGENT_SESSION_ID] || '');
    runChatAction(sid, {
      kind: 'success',
      title: 'Switched chat',
      message: `Resuming Claude session ${sid.slice(0, 8)}… on the next message.`,
    });
  }

  // Precompute rows (no logic inside JSX). Empty ready-state still shows
  // the "New chat" action — a task whose chat has no session id yet
  // simply has nothing to navigate back to.
  let listContent = null;
  if (state.status === 'loading') {
    listContent = <p className="chats-menu-empty">Loading chats…</p>;
  } else if (state.status === 'error') {
    listContent = <p className="chats-menu-empty error">{state.error}</p>;
  } else if (state.status === 'ready' && state.chats.length === 0) {
    listContent = (
      <p className="chats-menu-empty">
        No chats yet — send a message to start one.
      </p>
    );
  } else if (state.status === 'ready') {
    listContent = state.chats.map((chat) => {
      const sid = String(chat[AGENT_SESSION_ID] || '');
      const title = chatTitle(chat);
      const meta = chatMeta(chat);
      return (
        <button
          key={sid}
          type="button"
          className={`chats-menu-row${chat.active ? ' is-active' : ''}`}
          onClick={() => onPickChat(chat)}
          disabled={busy}
          // No tooltip on the active row: it already SAYS "current" beside
          // the agent chip, and a hover label repeating that only had
          // somewhere to be clipped. The switch hint stays, because
          // "clicking this replaces your current chat" is not visible.
          title={chat.active
            ? undefined
            : 'Switch back to this chat — the next message resumes it.'}
        >
          <span className="chats-menu-row-title">{title}</span>
          <span className="chats-menu-row-meta">
            <AgentBackendChip backend={chat.agent_backend} />
            {meta}
          </span>
        </button>
      );
    });
  }

  const confirmWarning = confirmTarget !== null ? (
    <p className="chats-menu-confirm" role="alert">
      Claude is mid-turn — switching kills the current run.
      Click the same chat again to confirm.
    </p>
  ) : null;

  const menu = open ? (
    <>
      <div
        className="chats-menu-backdrop"
        onClick={close}
        aria-hidden="true"
      />
      <div className="chats-menu" role="menu">
        <button
          type="button"
          className="chats-menu-new"
          onClick={() => onNewChat(agentBackend)}
          disabled={busy}
        >
          New chat
        </button>
        {confirmWarning}
        {listContent}
      </div>
    </>
  ) : null;

  return (
    <span className="chats-menu-wrap">
      <button
        id="session-chats"
        type="button"
        // ``tooltip-end`` — right-anchored, growing LEFTWARD.
        //
        // The anchor has to follow the button, and this button has moved:
        // it used to sit near the chat panel's left edge (where
        // ``tooltip-start`` was right), and now sits at the RIGHT end of the
        // agent chat bar, where a left-anchored tooltip runs off the panel.
        // The dropdown's own anchor was already moved for the same reason —
        // this is its tooltip, on the same button, and it was left behind.
        className={`session-action${open ? '' : ' tooltip-end'}`}
        // Only while CLOSED. An open menu already shows what the button
        // explains, and the tooltip is anchored to the button — so it
        // renders on top of the very list the operator is reading.
        data-tooltip={open ? undefined : 'Chats — start a new conversation, or go back to an earlier one. Old chats are kept and can be resumed.'}
        onClick={toggle}
        aria-expanded={open}
        aria-label="Chats"
      >
        {/* Line, not solid: it sits beside the chat's own title on a text
            row, where a filled glyph reads as a stamped blob. */}
        <Icon name="chat-line" />
      </button>
      {menu}
    </span>
  );
}

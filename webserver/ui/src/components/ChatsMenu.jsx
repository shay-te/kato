import { useEffect, useRef, useState } from 'react';
import AgentBackendChip, { backendLabel } from './AgentBackendChip.jsx';
import { fetchTaskChats, renameTaskChat, startTaskChat } from '../api.js';
import { AGENT_SESSION_ID } from '../constants/sessionFields.js';
import { useEscapeKey } from '../hooks/useEscapeKey.js';
import { toast } from '../stores/toastStore.js';
import { chatMeta, chatTitle } from './ChatsMenuHelpers.js';
import AdoptSessionModal from './AdoptSessionModal.jsx';
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
  onSessionAdopted = null,
  // Whether this backend keeps conversations on THIS machine. OpenHands runs
  // its sessions server-side, so offering adoption there opens a picker that
  // can only ever come back empty. Passed down rather than fetched here: the
  // tab strip already has the backend list, and a second request for the
  // same answer is exactly the duplication the API pass removed.
  //
  // Defaults TRUE — an unknown answer shows the control, because hiding a
  // working feature is the worse error of the two.
  supportsAdoption = true,
  turnInFlight = false,
}) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState({ status: 'idle', chats: [], error: '' });
  const [busy, setBusy] = useState(false);
  // Mid-turn guard: switching chats KILLS the live subprocess. When Claude
  // is mid-turn, the first click arms this with the requested target and
  // shows a warning; only a second click on the same target proceeds.
  const [confirmTarget, setConfirmTarget] = useState(null);
  const [adoptOpen, setAdoptOpen] = useState(false);
  // The chat being renamed, and the text so far. ``null`` = nobody is. The
  // list derives its label from the first user message otherwise, which is a
  // reasonable guess and a poor name.
  const [renaming, setRenaming] = useState(null);
  // Escape cancels, but the input also commits on blur — and clearing the
  // rename state unmounts the input, which fires that blur. Without this the
  // cancel handler's own teardown would save the text the operator just
  // discarded. A ref, not state: the blur runs before a re-render would
  // deliver a new value.
  const renameCancelledRef = useRef(false);

  function close() {
    setOpen(false);
    setConfirmTarget(null);
    // Drop a half-typed name rather than keeping it armed for the next open,
    // where it would reappear over a row the operator has moved on from.
    // Closing is a cancel, not a save — same teardown-fires-blur reason.
    renameCancelledRef.current = true;
    setRenaming(null);
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

  async function commitRename() {
    if (renameCancelledRef.current) {
      renameCancelledRef.current = false;
      return;
    }
    if (!renaming) { return; }
    const { sid, value } = renaming;
    setRenaming(null);
    const result = await renameTaskChat(taskId, sid, value);
    if (!result.ok) {
      toast.errorFromResult(result, {
        title: 'Rename failed', fallback: 'unknown error',
      });
      return;
    }
    // Re-read rather than patching the row: an empty name CLEARS it, and the
    // label then falls back to the derived preview, which only the server
    // knows how to produce.
    loadChats();
    if (typeof onChatChanged === 'function') { onChatChanged({}); }
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
      if (renaming && renaming.sid === sid) {
        return (
          <form
            key={sid}
            className="chats-menu-row is-renaming"
            onSubmit={(e) => { e.preventDefault(); commitRename(); }}
          >
            <input
              className="chats-menu-rename-input"
              value={renaming.value}
              autoFocus
              aria-label="Chat name"
              placeholder="Name this chat — empty clears it"
              maxLength={120}
              onChange={(e) => setRenaming({ sid, value: e.target.value })}
              onKeyDown={(e) => {
                // Escape cancels the rename WITHOUT closing the menu — the
                // drawer's own Escape handler would otherwise take the whole
                // menu down and lose the operator's place in the list.
                if (e.key === 'Escape') {
                  e.stopPropagation();
                  renameCancelledRef.current = true;
                  setRenaming(null);
                }
              }}
              onBlur={commitRename}
            />
          </form>
        );
      }
      return (
        <div
          key={sid}
          className={`chats-menu-row-wrap${chat.active ? ' is-active' : ''}`}
        >
          <button
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
          <button
            type="button"
            className="chats-menu-rename"
            // Seeded with the STORED name, not the displayed label: opening
            // the box on a never-renamed chat should offer an empty field,
            // not the first-message preview for the operator to delete.
            onClick={() => setRenaming({ sid, value: String(chat.name || '') })}
            disabled={busy}
            aria-label={`Rename ${title}`}
            title="Rename this chat"
          >
            <span aria-hidden="true">✎</span>
          </button>
        </div>
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
        {/* Adoption belongs HERE rather than in the header toolbar it used to
            sit in. It is a per-chat action on one backend — the toolbar had
            no backend in scope, so the one button could only ever mean
            Claude, and a Codex operator had no way to reach it at all. */}
        {supportsAdoption ? (
          <button
            type="button"
            className="chats-menu-adopt"
            onClick={() => { setAdoptOpen(true); close(); }}
            disabled={busy}
          >
            Adopt existing {backendLabel(agentBackend) || 'agent'} session…
          </button>
        ) : null}
        {confirmWarning}
        {listContent}
      </div>
    </>
  ) : null;

  const adoptModal = adoptOpen ? (
    <AdoptSessionModal
      taskId={taskId}
      agentBackend={agentBackend}
      onClose={() => setAdoptOpen(false)}
      onAdopted={(adopted) => {
        setAdoptOpen(false);
        // The adopted id becomes this tab's active chat, so the menu's
        // list is stale the moment adoption succeeds.
        //
        // Pass the adopted row, do NOT call this bare: an argument-less
        // call reads as "no session id", which makes the chat announce
        // "🆕 new chat — your next message starts a fresh session" one line
        // above the "📎 session attached" bubble. The operator reads the
        // contradiction first, and it is the opposite of what just happened.
        if (typeof onChatChanged === 'function') { onChatChanged(adopted); }
        if (typeof onSessionAdopted === 'function') { onSessionAdopted(adopted); }
      }}
    />
  ) : null;

  return (
    <span className="chats-menu-wrap">
      {adoptModal}
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

import { useState } from 'react';
import { fetchTaskChats, startTaskChat } from '../api.js';
import { AGENT_SESSION_ID } from '../constants/sessionFields.js';
import { toast } from '../stores/toastStore.js';
import Icon from './Icon.jsx';

// Header dropdown for navigating a task's chats. "New chat" detaches the
// current conversation (the next message spawns a fresh Claude session);
// picking a previous chat resumes that conversation instead. The detached
// chat is never lost — it stays in the list and can be returned to.
export default function ChatsMenu({ taskId, onChatChanged }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState({ status: 'idle', chats: [], error: '' });
  const [busy, setBusy] = useState(false);

  async function loadChats() {
    setState({ status: 'loading', chats: [], error: '' });
    try {
      const body = await fetchTaskChats(taskId);
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
    if (next) { loadChats(); }
  }

  async function runChatAction(agentSessionId, successToast) {
    if (busy) { return; }
    setBusy(true);
    try {
      const result = await startTaskChat(taskId, agentSessionId);
      if (!result.ok) {
        toast.errorFromResult(result, {
          title: 'Chat action failed', fallback: 'unknown error',
        });
        return;
      }
      setOpen(false);
      toast.show(successToast);
      if (typeof onChatChanged === 'function') {
        onChatChanged(result.body || {});
      }
    } finally {
      setBusy(false);
    }
  }

  function onNewChat() {
    runChatAction('', {
      kind: 'success',
      title: 'New chat started',
      message: 'The previous conversation is kept in the chats menu — '
        + 'your next message starts a fresh Claude session.',
    });
  }

  function onPickChat(chat) {
    if (chat.active) {
      setOpen(false);
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
      const preview = chat.first_user_message || '(no messages yet)';
      const turns = Number(chat.turn_count) || 0;
      return (
        <button
          key={sid}
          type="button"
          className={`chats-menu-row${chat.active ? ' is-active' : ''}`}
          onClick={() => onPickChat(chat)}
          disabled={busy}
          title={chat.active
            ? 'This is the current chat.'
            : 'Switch back to this chat — the next message resumes it.'}
        >
          <span className="chats-menu-row-sid">{sid.slice(0, 8)}…</span>
          <span className="chats-menu-row-preview">{preview}</span>
          <span className="chats-menu-row-meta">
            {chat.active ? 'current' : `${turns} turn${turns === 1 ? '' : 's'}`}
          </span>
        </button>
      );
    });
  }

  const menu = open ? (
    <>
      <div
        className="chats-menu-backdrop"
        onClick={() => setOpen(false)}
        aria-hidden="true"
      />
      <div className="chats-menu" role="menu">
        <button
          type="button"
          className="chats-menu-new"
          onClick={onNewChat}
          disabled={busy}
        >
          <Icon name="plus" /> New chat
        </button>
        {listContent}
      </div>
    </>
  ) : null;

  return (
    <span className="chats-menu-wrap">
      <button
        id="session-chats"
        type="button"
        className="session-action tooltip-below"
        data-tooltip="Chats — start a fresh conversation for this task, or navigate back to a previous one. The old chat is kept and can be resumed any time."
        onClick={toggle}
        aria-expanded={open}
        aria-label="Chats"
      >
        <Icon name="comment" />
      </button>
      {menu}
    </span>
  );
}

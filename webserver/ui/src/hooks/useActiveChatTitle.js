import { useEffect, useState } from 'react';
import { fetchTaskChats } from '../api.js';
import { chatTitle } from '../components/ChatsMenuHelpers.js';

// Title of the chat a task is CURRENTLY on, for the bar under the agent tabs.
//
// The chats dropdown only loads its list when opened, so the name of the
// conversation you are in was visible nowhere until you went looking for it.
// This is the same endpoint that dropdown uses, fetched once per
// (task, backend) and again whenever ``refreshKey`` changes — which the tab
// strip bumps after a new-chat / switch, so the bar never names the chat you
// just left.
//
// Returns '' while loading, on error, and for a task with no chat yet — the
// bar falls back to a neutral label rather than showing a wrong name.
export function useActiveChatTitle(taskId, agentBackend = '', refreshKey = 0) {
  const [title, setTitle] = useState('');

  useEffect(() => {
    if (!taskId) { setTitle(''); return undefined; }
    let cancelled = false;
    fetchTaskChats(taskId, agentBackend)
      .then((body) => {
        if (cancelled) { return; }
        const chats = Array.isArray(body?.chats) ? body.chats : [];
        const active = chats.find((chat) => chat?.active);
        setTitle(active ? chatTitle(active) : '');
      })
      .catch(() => { if (!cancelled) { setTitle(''); } });
    return () => { cancelled = true; };
  }, [taskId, agentBackend, refreshKey]);

  return title;
}

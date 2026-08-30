import { useEffect, useState } from 'react';
import { fetchTaskChats } from '../api.js';
import { chatTitle } from '../components/ChatsMenuHelpers.js';
import { AGENT_SESSION_ID } from '../constants/sessionFields.js';

// Title of the chat a task is CURRENTLY on, for the bar under the agent tabs.
//
// The chats dropdown only loads its list when opened, so the name of the
// conversation you are in was visible nowhere until you went looking for it.
// This is the same endpoint that dropdown uses, fetched once per
// (task, backend) and again whenever ``refreshKey`` changes — which the tab
// strip bumps after a new-chat / switch, so the bar never names the chat you
// just left.
//
// Returns ``{ title, agentSessionId }`` — both '' while loading, on error, and
// for a task with no chat yet, so the bar falls back to a neutral label rather
// than showing a wrong name.
//
// The SESSION ID comes from here too, and that is the point rather than a
// convenience: it is a per-backend fact. It used to be rendered once in the
// global task header, which could only ever name one backend's session — so
// on a task with both a Claude and a Codex chat the header showed one id and
// silently implied it belonged to whichever tab you were looking at.
const EMPTY = { title: '', agentSessionId: '' };

export function useActiveChat(taskId, agentBackend = '', refreshKey = 0) {
  const [active, setActive] = useState(EMPTY);

  useEffect(() => {
    if (!taskId) { setActive(EMPTY); return undefined; }
    let cancelled = false;
    fetchTaskChats(taskId, agentBackend)
      .then((body) => {
        if (cancelled) { return; }
        const chats = Array.isArray(body?.chats) ? body.chats : [];
        const current = chats.find((chat) => chat?.active);
        setActive(current ? {
          title: chatTitle(current),
          agentSessionId: String(current[AGENT_SESSION_ID] || ''),
        } : EMPTY);
      })
      .catch(() => { if (!cancelled) { setActive(EMPTY); } });
    return () => { cancelled = true; };
  }, [taskId, agentBackend, refreshKey]);

  return active;
}

// Back-compat for callers that only want the label.
export function useActiveChatTitle(taskId, agentBackend = '', refreshKey = 0) {
  return useActiveChat(taskId, agentBackend, refreshKey).title;
}

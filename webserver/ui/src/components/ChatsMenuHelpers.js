import { AGENT_SESSION_ID } from '../constants/sessionFields.js';

export function chatTitle(chat) {
  // An operator-given name always wins. The first-user-message preview below
  // is a reasonable GUESS and a poor name — the opening line of a
  // conversation is usually the least memorable thing about it, and two
  // chats that began "fix the failing test" are indistinguishable a week on.
  const named = String(chat?.name || '').trim();
  if (named) { return named; }
  const preview = String(chat?.first_user_message || '').trim();
  if (preview) { return preview; }
  const sid = String(chat?.[AGENT_SESSION_ID] || '').trim();
  if (sid) { return `Chat ${sid.slice(0, 8)}...`; }
  return 'Untitled chat';
}

export function chatMeta(chat) {
  if (chat?.active) { return 'current'; }
  const turns = Number(chat?.turn_count) || 0;
  return `${turns} turn${turns === 1 ? '' : 's'}`;
}

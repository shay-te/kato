import React from 'react';

// Which CLI produced a chat. Read from the chat's own record, never from the
// current backend setting: an operator who switches backends still has their
// older conversations, and each one resumes through the CLI that wrote it.
//
// Renders nothing when the backend is unknown — chats created before kato
// recorded this have no honest answer, and a guessed chip is worse than none.
const LABELS = {
  claude: 'Claude',
  codex: 'Codex',
  openhands: 'OpenHands',
};

export function backendLabel(backend) {
  const key = String(backend || '').trim().toLowerCase();
  if (!key) { return ''; }
  return LABELS[key] || key;
}

export default function AgentBackendChip({ backend }) {
  const label = backendLabel(backend);
  if (!label) { return null; }
  const key = String(backend).trim().toLowerCase();
  return (
    <span
      className={`agent-backend-chip agent-backend-chip-${key}`}
      title={`This chat runs on ${label}`}
    >
      {label}
    </span>
  );
}

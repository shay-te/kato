import { useCallback, useState } from 'react';
import EventLog from './EventLog.jsx';
import MessageForm from './MessageForm.jsx';
import PermissionDecisionContainer from './PermissionDecisionContainer.jsx';
import SessionHeader from './SessionHeader.jsx';
import WorkingIndicator from './WorkingIndicator.jsx';
import { ChatComposerContext } from '../contexts/ChatComposerContext.jsx';
import { useSessionStream, SESSION_LIFECYCLE } from '../hooks/useSessionStream.js';
import { useToolMemory } from '../hooks/useToolMemory.js';
import { postSession } from '../api.js';

export default function SessionDetail({ session, onActivity }) {
  const taskId = session?.task_id;
  const [composerValue, setComposerValue] = useState('');
  const stream = useSessionStream(taskId, onActivity);
  const memory = useToolMemory();

  const appendToInput = useCallback((fragment) => {
    if (!fragment) { return; }
    setComposerValue((current) => {
      const needsLeadingSpace = current && !/\s$/.test(current);
      return current + (needsLeadingSpace ? ' ' : '') + fragment;
    });
  }, []);

  const composerContextValue = { appendToInput };

  if (!session) {
    return (
      <ChatComposerContext.Provider value={composerContextValue}>
        <main id="session-pane">
          <section id="session-placeholder" className="placeholder">
            Select a tab to chat with the bound Claude session.
          </section>
        </main>
      </ChatComposerContext.Provider>
    );
  }

  async function onSendMessage(text) {
    stream.appendLocalEvent({ source: 'local', kind: 'user', text });
    stream.markTurnBusy(true);
    const result = await postSession(taskId, 'messages', { text });
    if (result.ok) {
      stream.appendLocalEvent({ source: 'local', kind: 'system', text: '✓ delivered' });
    } else {
      stream.appendLocalEvent({
        source: 'local', kind: 'error',
        text: `send failed: ${result.error}`,
      });
      stream.markTurnBusy(false);
    }
  }

  async function submitPermissionResponse({ requestId, allow, rationale }) {
    const result = await postSession(taskId, 'permission', {
      request_id: requestId,
      allow,
      rationale,
    });
    if (!result.ok) {
      stream.appendLocalEvent({
        source: 'local', kind: 'error',
        text: `permission send failed: ${result.error}`,
      });
    }
  }

  async function onStopped(result) {
    stream.appendLocalEvent(
      result.ok
        ? { source: 'local', kind: 'system', text: '✗ session stopped' }
        : { source: 'local', kind: 'error', text: `stop failed: ${result.error}` },
    );
  }

  return (
    <ChatComposerContext.Provider value={composerContextValue}>
      <main id="session-pane">
        <section id="session-detail">
          <SessionHeader session={session} onStopped={onStopped} />
          <EventLog
            entries={stream.events}
            banner={lifecycleBanner(
              stream.lifecycle,
              taskId,
              hasVisibleBubbles(stream.events),
            )}
          />
          <WorkingIndicator active={stream.turnInFlight} />
          <MessageForm
            value={composerValue}
            onChange={setComposerValue}
            turnInFlight={stream.turnInFlight}
            onSubmit={onSendMessage}
            disabled={!isLive(stream.lifecycle)}
            disabledReason={composerDisabledReason(stream.lifecycle)}
          />
        </section>
        <PermissionDecisionContainer
          pending={stream.pendingPermission}
          onDismiss={stream.dismissPermission}
          onSubmit={submitPermissionResponse}
          onAuditBubble={stream.appendLocalEvent}
          recallToolDecision={memory.recall}
          rememberToolDecision={memory.remember}
        />
      </main>
    </ChatComposerContext.Provider>
  );
}

function isLive(lifecycle) {
  return lifecycle === SESSION_LIFECYCLE.STREAMING
    || lifecycle === SESSION_LIFECYCLE.CONNECTING;
}

function composerDisabledReason(lifecycle) {
  switch (lifecycle) {
    case SESSION_LIFECYCLE.IDLE:
      return 'No live subprocess — chat will resume when kato re-spawns this task.';
    case SESSION_LIFECYCLE.CLOSED:
      return 'Session has ended.';
    case SESSION_LIFECYCLE.MISSING:
      return 'No record for this task on the server.';
    default:
      return '';
  }
}

// Banner is the always-visible status line at the top of the log.
// - CONNECTING / IDLE / MISSING / CLOSED → always show the explanatory text.
// - STREAMING → show "Connected, waiting…" *only* until at least one
//   bubble appears, then suppress so the chat reads cleanly.
function lifecycleBanner(lifecycle, taskId, hasVisible) {
  switch (lifecycle) {
    case SESSION_LIFECYCLE.CONNECTING:
      return `Connecting to session for ${taskId}…`;
    case SESSION_LIFECYCLE.STREAMING:
      return hasVisible
        ? null
        : `Connected — waiting for Claude's first reply…`;
    case SESSION_LIFECYCLE.IDLE:
      return '(no live subprocess for this tab — chat will resume when kato re-spawns it)';
    case SESSION_LIFECYCLE.MISSING:
      return 'No record for this task on the server.';
    case SESSION_LIFECYCLE.CLOSED:
      return '(session ended)';
    default:
      return null;
  }
}

// True when at least one entry would produce a visible bubble. Used by
// the banner so we don't show "waiting for first reply" once chat
// content actually arrives. Mirrors EventLog's filtering rules.
function hasVisibleBubbles(entries) {
  return entries.some((entry) => {
    if (entry?.source === 'local') { return true; }
    const type = entry?.raw?.type;
    if (!type) { return false; }
    if (type === 'user' || type === 'stream_event') { return false; }
    if (type === 'permission_request' || type === 'control_request' || type === 'permission_response') { return false; }
    if (type === 'system' && entry.raw.subtype !== 'init') { return false; }
    if (type === 'assistant') {
      const content = entry.raw?.message?.content || [];
      return content.some(
        (b) => (b?.type === 'text' && b.text) || b?.type === 'tool_use',
      );
    }
    return true;
  });
}

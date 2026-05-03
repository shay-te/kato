import EventLog from './EventLog.jsx';
import MessageForm from './MessageForm.jsx';
import PermissionDecisionContainer from './PermissionDecisionContainer.jsx';
import SessionHeader from './SessionHeader.jsx';
import WorkingIndicator from './WorkingIndicator.jsx';
import { BUBBLE_KIND } from '../constants/bubbleKind.js';
import { CLAUDE_EVENT, CLAUDE_SYSTEM_SUBTYPE } from '../constants/claudeEvent.js';
import { ENTRY_SOURCE } from '../constants/entrySource.js';
import { useSessionStream, SESSION_LIFECYCLE } from '../hooks/useSessionStream.js';
import { useToolMemory } from '../hooks/useToolMemory.js';
import { postSession } from '../api.js';

export default function SessionDetail({
  session,
  onActivity,
  needsAttention = false,
  composerValue = '',
  onComposerChange,
}) {
  const taskId = session?.task_id;
  const stream = useSessionStream(taskId, onActivity);
  const memory = useToolMemory();
  const updateComposer = typeof onComposerChange === 'function' ? onComposerChange : noop;

  if (!session) {
    return (
      <main id="session-pane">
        <section id="session-placeholder" className="placeholder">
          Select a tab to chat with the bound Claude session.
        </section>
      </main>
    );
  }

  async function onSendMessage(text) {
    stream.appendLocalEvent({ source: ENTRY_SOURCE.LOCAL, kind: BUBBLE_KIND.USER, text });
    stream.markTurnBusy(true);
    const result = await postSession(taskId, 'messages', { text });
    if (result.ok) {
      const status = result.body?.status;
      if (status === 'spawned') {
        stream.appendLocalEvent({
          source: ENTRY_SOURCE.LOCAL, kind: BUBBLE_KIND.SYSTEM,
          text: '✓ resumed — spawning Claude…',
        });
        stream.reconnect();
      } else {
        stream.appendLocalEvent({
          source: ENTRY_SOURCE.LOCAL, kind: BUBBLE_KIND.SYSTEM, text: '✓ delivered',
        });
      }
    } else {
      stream.appendLocalEvent({
        source: ENTRY_SOURCE.LOCAL, kind: BUBBLE_KIND.ERROR,
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
        source: ENTRY_SOURCE.LOCAL, kind: BUBBLE_KIND.ERROR,
        text: `permission send failed: ${result.error}`,
      });
    }
  }

  async function onStopped(result) {
    stream.appendLocalEvent(
      result.ok
        ? { source: ENTRY_SOURCE.LOCAL, kind: BUBBLE_KIND.SYSTEM, text: '✗ session stopped' }
        : { source: ENTRY_SOURCE.LOCAL, kind: BUBBLE_KIND.ERROR, text: `stop failed: ${result.error}` },
    );
  }

  const banner = lifecycleBanner(
    stream.lifecycle,
    taskId,
    hasVisibleBubbles(stream.events),
  );
  const composerDisabled = !canSend(stream.lifecycle, session);
  const composerHint = composerDisabledReason(stream.lifecycle, session);
  return (
    <main id="session-pane">
      <section id="session-detail">
        <SessionHeader
          session={session}
          needsAttention={needsAttention}
          onStopped={onStopped}
          streamLifecycle={stream.lifecycle}
          turnInFlight={stream.turnInFlight}
        />
        <EventLog entries={stream.events} banner={banner} />
        <WorkingIndicator
          active={stream.turnInFlight}
          lastEventAt={stream.lastEventAt}
        />
        <MessageForm
          value={composerValue}
          onChange={updateComposer}
          turnInFlight={stream.turnInFlight}
          onSubmit={onSendMessage}
          disabled={composerDisabled}
          disabledReason={composerHint}
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
  );
}

function noop() {}

function canSend(lifecycle, session) {
  // Only block when the server has no record at all. CLOSED/IDLE still
  // accept sends — the backend respawns Claude on demand, and after a
  // rate-limit hit the operator needs to be able to retry once the
  // window resets without manually refreshing.
  if (lifecycle === SESSION_LIFECYCLE.MISSING) { return false; }
  return true;
}

function composerDisabledReason(lifecycle, session) {
  if (canSend(lifecycle, session)) { return ''; }
  return 'No record for this task on the server.';
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
    if (entry?.source === ENTRY_SOURCE.LOCAL) { return true; }
    if (entry?.source === ENTRY_SOURCE.HISTORY) { return true; }
    const type = entry?.raw?.type;
    if (!type) { return false; }
    if (type === CLAUDE_EVENT.USER || type === CLAUDE_EVENT.STREAM_EVENT) { return false; }
    if (type === CLAUDE_EVENT.PERMISSION_REQUEST
        || type === CLAUDE_EVENT.CONTROL_REQUEST
        || type === CLAUDE_EVENT.PERMISSION_RESPONSE) { return false; }
    if (type === CLAUDE_EVENT.SYSTEM && entry.raw.subtype !== CLAUDE_SYSTEM_SUBTYPE.INIT) {
      return false;
    }
    if (type === CLAUDE_EVENT.ASSISTANT) {
      const content = entry.raw?.message?.content || [];
      return content.some(
        (b) => (b?.type === 'text' && b.text) || b?.type === 'tool_use',
      );
    }
    return true;
  });
}

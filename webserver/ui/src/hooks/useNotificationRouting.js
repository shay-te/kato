import { useCallback, useEffect, useRef } from 'react';
import { fetchPendingPermissions } from '../api.js';
import { CLAUDE_EVENT } from '../constants/claudeEvent.js';
import { NOTIFICATION_KIND } from '../constants/notificationKind.js';
import { classifyStatusEntry } from '../utils/classifyStatusEntry.js';
import { unpackPermissionEnvelope } from '../utils/permissionEnvelope.js';
import { maybePlayPermissionChime } from '../utils/permissionSound.js';

// How long to wait before the second look at the pending list. The ask
// may not be registered on its session yet when the status line lands,
// and the webserver's auto-resolve runs on the first look, so one round
// trip can legitimately answer "nothing pending" either way.
const PENDING_CONFIRM_RETRY_MS = 1500;

// Does this task have a permission ask that a HUMAN still has to answer?
//
// The status feed's "claude is asking permission to run X" line is
// emitted the instant Claude asks (claude_core_lib's
// ``_log_event_for_operator``) — BEFORE the webserver checks whether a
// remembered decision already covers it. So the line alone cannot tell
// an ask that auto-approves itself from one that needs the operator, and
// pinging for the former is exactly the reported noise: a browser
// notification for something already approved by the time the operator
// switches to the kato tab. Guessing from a client-side cache of saved
// decisions (what this used to do) can't close the gap either — the
// status line carries no command, so a per-command Bash grant looks
// un-remembered.
//
// ``/api/permissions/pending`` IS the distinction: the route runs the
// same server-side auto-resolve per ask and lists only what still needs
// a human. Ask it, and ping only for what it lists.
//
// Fails LOUD: an unreachable backend notifies. A false ping costs the
// operator a glance; a missed one costs an agent every minute it sits
// blocked with nobody watching.
async function permissionNeedsOperator(taskId, wait) {
  const target = String(taskId || '');
  if (!target) { return true; }
  for (let attempt = 0; attempt < 2; attempt += 1) {
    if (attempt > 0) { await wait(PENDING_CONFIRM_RETRY_MS); }
    let body;
    try {
      body = await fetchPendingPermissions();
    } catch (_) {
      return true;
    }
    const list = Array.isArray(body?.pending) ? body.pending : [];
    const waiting = list.some(
      (envelope) => String(unpackPermissionEnvelope(envelope).taskId) === target,
    );
    if (waiting) { return true; }
  }
  return false;
}

function defaultWait(ms) {
  return new Promise((resolve) => { setTimeout(resolve, ms); });
}

// ``activeTaskId`` (optional): the focused task. Its permission ask is
// already notified by ``onSessionEvent`` off the live SSE stream, and
// the status feed emits a duplicate "asking permission to run X" line
// for that SAME ask — suppressed here for the focused task (owned by
// onSessionEvent). Background tasks have no SSE stream in the browser,
// so the status feed is their ONLY notifier; theirs is confirmed
// against the pending list above before it pings.
//
// The per-task SSE path needs no such check: the webserver auto-resolves
// a matching pending request against the remembered-decision store
// before it is ever published over SSE (see _maybe_auto_resolve_live_event
// in kato_webserver/app.py), so any control_request/permission_request
// this hook receives from the live stream already needs a human.
//
// ``wait`` (optional): sleep function, injected by the tests so the
// retry above doesn't cost them real seconds.
export function useNotificationRouting(
  notify,
  { activeTaskId, wait = defaultWait } = {},
) {
  // Hold the focused task in a ref so switching tabs does NOT change the
  // callback identity (which would re-subscribe the status feed).
  const activeTaskIdRef = useRef(activeTaskId || '');
  useEffect(() => {
    activeTaskIdRef.current = activeTaskId || '';
  }, [activeTaskId]);

  const onStatusEntry = useCallback((entry) => {
    const classification = classifyStatusEntry(entry);
    if (!classification) { return; }
    if (classification.permissionTool) {
      // onSessionEvent already owns the focused task's permission ping.
      if (classification.taskId
          && classification.taskId === activeTaskIdRef.current) {
        return;
      }
      // Background task: ping only once the backend confirms the ask is
      // still waiting on a human (see permissionNeedsOperator).
      permissionNeedsOperator(classification.taskId, wait).then((needed) => {
        if (!needed) { return; }
        // Chime honours the operator's sound prefs + focus mode internally.
        maybePlayPermissionChime(
          `${classification.taskId || ''}:${classification.permissionTool}`,
        );
        notify(classification);
      });
      return;
    }
    notify(classification);
  }, [notify, wait]);

  const onSessionEvent = useCallback((raw, taskId) => {
    if (!raw?.type) { return; }
    if (raw.type === CLAUDE_EVENT.PERMISSION_REQUEST
        || raw.type === CLAUDE_EVENT.CONTROL_REQUEST) {
      // The webserver already auto-resolved this against a remembered
      // decision before publishing it over SSE if it could — reaching
      // here means it genuinely needs the operator, so always chime +
      // notify (no client-side recall check needed).
      const { toolName, requestId } = unpackPermissionEnvelope(raw);
      // The dedupe key (request id) collapses this with the status feed's
      // duplicate line for the same ask.
      maybePlayPermissionChime(requestId || `${taskId || ''}:${toolName}`);
      notify({
        title: 'Approval needed',
        body: toolName,
        taskId,
        kind: NOTIFICATION_KIND.ATTENTION,
      });
      return;
    }
    if (raw.type === CLAUDE_EVENT.RESULT) {
      const ok = !raw.is_error;
      const summary = typeof raw.result === 'string'
        ? raw.result.slice(0, 140)
        : '';
      notify({
        title: ok ? 'Claude replied' : 'Turn failed',
        body: summary,
        taskId,
        kind: ok ? NOTIFICATION_KIND.REPLY : NOTIFICATION_KIND.ERROR,
      });
    }
  }, [notify]);

  return { onStatusEntry, onSessionEvent };
}

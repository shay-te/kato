import { useCallback, useEffect, useRef } from 'react';
import { CLAUDE_EVENT } from '../constants/claudeEvent.js';
import { NOTIFICATION_KIND } from '../constants/notificationKind.js';
import { classifyStatusEntry } from '../utils/classifyStatusEntry.js';
import { unpackPermissionEnvelope } from '../utils/permissionEnvelope.js';
import { maybePlayPermissionChime } from '../utils/permissionSound.js';

// ``recallToolDecision`` (optional): ``(toolName, command) => 'allow' | 'deny' | null``,
// reading the BACKEND's remembered-decision cache (see
// useRememberedToolDecisions — the browser holds no decision of its
// own). Only needed for the STATUS FEED path below: that "asking
// permission to run X" log line fires unconditionally the moment
// Claude asks (claude_core_lib's _log_event_for_operator), before the
// webserver's own auto-resolve check runs, so it can't tell a
// several-times-in-a-row auto-approved Bash call apart from one that
// genuinely needs the operator — pinging for a decision already made
// is noise (the reported "I get browser notification approval needed
// even when claude is approving automatically from saved rules" bug).
//
// The per-task SSE path (``onSessionEvent`` below) needs NO such
// check any more: the webserver auto-resolves a matching pending
// request against the SAME remembered-decision store before it is
// ever published over SSE (see _maybe_auto_resolve_live_event in
// kato_webserver/app.py), so any control_request/permission_request
// this hook receives from the live stream already needs a human.
//
// ``activeTaskId`` (optional): the focused task. Its permission ask is
// already notified by ``onSessionEvent`` off the live SSE stream, and
// the status feed emits a duplicate "asking permission to run X" line
// for that SAME ask — suppressed here for the focused task (owned by
// onSessionEvent) and, for background tasks, best-effort suppressed
// when a saved decision exists.
export function useNotificationRouting(
  notify,
  { recallToolDecision, activeTaskId } = {},
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
      // Background task: the status line lacks the command, so this only
      // catches non-command-keyed tools and bare-tool grants — but that is
      // exactly the "saved before" case the operator reported.
      const decision = typeof recallToolDecision === 'function'
        ? recallToolDecision(classification.permissionTool, '')
        : null;
      if (decision === 'allow' || decision === 'deny') { return; }
      // A real, un-remembered permission ask on a BACKGROUND task → chime
      // (honours the operator's sound prefs + focus mode internally).
      maybePlayPermissionChime(
        `${classification.taskId || ''}:${classification.permissionTool}`,
      );
    }
    notify(classification);
  }, [notify, recallToolDecision]);

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

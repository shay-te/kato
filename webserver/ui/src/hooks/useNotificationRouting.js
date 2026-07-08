import { useCallback, useEffect, useRef } from 'react';
import { CLAUDE_EVENT } from '../constants/claudeEvent.js';
import { NOTIFICATION_KIND } from '../constants/notificationKind.js';
import { classifyStatusEntry } from '../utils/classifyStatusEntry.js';
import {
  unpackPermissionEnvelope,
  decisionCommandFor,
} from '../utils/permissionEnvelope.js';
import { maybePlayPermissionChime } from '../utils/permissionSound.js';

// ``recallToolDecision`` (optional): ``(toolName) => 'allow' | 'deny' | null``.
// When set AND the recall returns a definitive decision, the permission ask
// will be auto-resolved silently by PermissionDecisionContainer and the
// browser notification ("Approval needed") MUST be suppressed — pinging the
// operator for an action they already decided is noise (the operator-
// reported "I get browser notification approval needed even when claude is
// approving automatically from saved rules" bug). Mirrors the tab-orange
// gate already in App.jsx, so the two surfaces stay aligned.
//
// ``activeTaskId`` (optional): the focused task. Its permission ask is
// already notified — with full command-level recall — by ``onSessionEvent``
// off the live SSE stream. The orchestrator status feed emits a duplicate
// "asking permission to run X" line for that SAME ask, and that line carries
// only the tool name, so it can't see a remembered ``(Bash, mvn)`` grant and
// would re-ping even when the SSE path correctly stayed silent. So the
// status-feed permission notification is suppressed for the focused task
// (owned by onSessionEvent) and, for background tasks, best-effort suppressed
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
      const { toolName, toolInput } = unpackPermissionEnvelope(raw);
      // Command-keyed tools (Bash) remember decisions by program
      // signature, not bare tool name — without ``decisionCommandFor``
      // the recall for ``Bash`` would miss ``(Bash, mvn)`` entries and
      // the notification would still fire for every remembered ``mvn``
      // ask. Mirror PermissionDecisionContainer's auto-resolve key so
      // a decision the auto-handler will silently honour suppresses the
      // notification too.
      const command = decisionCommandFor(toolName, toolInput);
      const decision = typeof recallToolDecision === 'function' && toolName
        ? recallToolDecision(toolName, command)
        : null;
      if (decision === 'allow' || decision === 'deny') { return; }
      // A real, un-remembered permission ask on the FOCUSED task → chime.
      // The dedupe key (request id) collapses this with the status feed's
      // duplicate line for the same ask.
      const { requestId } = unpackPermissionEnvelope(raw);
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
  }, [notify, recallToolDecision]);

  return { onStatusEntry, onSessionEvent };
}

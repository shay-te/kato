import { useCallback } from 'react';
import { CLAUDE_EVENT } from '../constants/claudeEvent.js';
import { NOTIFICATION_KIND } from '../constants/notificationKind.js';
import { classifyStatusEntry } from '../utils/classifyStatusEntry.js';
import {
  unpackPermissionEnvelope,
  decisionCommandFor,
} from '../utils/permissionEnvelope.js';

// ``recallToolDecision`` (optional): ``(toolName) => 'allow' | 'deny' | null``.
// When set AND the recall returns a definitive decision, the permission ask
// will be auto-resolved silently by PermissionDecisionContainer and the
// browser notification ("Approval needed") MUST be suppressed — pinging the
// operator for an action they already decided is noise (the operator-
// reported "I get browser notification approval needed even when claude is
// approving automatically from saved rules" bug). Mirrors the tab-orange
// gate already in App.jsx, so the two surfaces stay aligned.
export function useNotificationRouting(notify, { recallToolDecision } = {}) {
  const onStatusEntry = useCallback((entry) => {
    const classification = classifyStatusEntry(entry);
    if (classification) { notify(classification); }
  }, [notify]);

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

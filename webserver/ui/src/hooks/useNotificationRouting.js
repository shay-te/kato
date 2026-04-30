import { useCallback } from 'react';
import { CLAUDE_EVENT } from '../constants/claudeEvent.js';
import { NOTIFICATION_KIND } from '../constants/notificationKind.js';
import { classifyStatusEntry } from '../utils/classifyStatusEntry.js';
import { unpackPermissionEnvelope } from '../utils/permissionEnvelope.js';

export function useNotificationRouting(notify) {
  const onStatusEntry = useCallback((entry) => {
    const classification = classifyStatusEntry(entry);
    if (classification) { notify(classification); }
  }, [notify]);

  const onSessionEvent = useCallback((raw, taskId) => {
    if (!raw?.type) { return; }
    if (raw.type === CLAUDE_EVENT.PERMISSION_REQUEST
        || raw.type === CLAUDE_EVENT.CONTROL_REQUEST) {
      notify({
        title: 'Approval needed',
        body: unpackPermissionEnvelope(raw).toolName,
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

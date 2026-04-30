import { useCallback } from 'react';
import { classifyStatusEntry } from '../utils/classifyStatusEntry.js';
import { unpackPermissionEnvelope } from '../utils/permissionEnvelope.js';

export function useNotificationRouting(notify) {
  const onStatusEntry = useCallback((entry) => {
    const classification = classifyStatusEntry(entry);
    if (classification) { notify(classification); }
  }, [notify]);

  const onSessionEvent = useCallback((raw, taskId) => {
    if (!raw?.type) { return; }
    if (raw.type === 'permission_request' || raw.type === 'control_request') {
      notify({
        title: 'Approval needed',
        body: unpackPermissionEnvelope(raw).toolName,
        taskId,
        kind: 'attention',
      });
      return;
    }
    if (raw.type === 'result') {
      const ok = !raw.is_error;
      const summary = typeof raw.result === 'string'
        ? raw.result.slice(0, 140)
        : '';
      notify({
        title: ok ? 'Claude replied' : 'Turn failed',
        body: summary,
        taskId,
        kind: ok ? 'reply' : 'error',
      });
    }
  }, [notify]);

  return { onStatusEntry, onSessionEvent };
}

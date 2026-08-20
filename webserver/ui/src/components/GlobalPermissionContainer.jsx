import { useCallback } from 'react';
import { useOperatorIsTyping } from '../hooks/useOperatorIsTyping.js';
import PermissionDecisionContainer from './PermissionDecisionContainer.jsx';
import { postSession } from '../api.js';
import { permissionStore } from '../stores/permissionStore.js';
import { usePendingPermissions } from '../hooks/usePendingPermissions.js';
import { unpackPermissionEnvelope } from '../utils/permissionEnvelope.js';

// The SINGLE owner of the permission-approval modal, for EVERY task.
//
// It renders the oldest pending ask from the shared ``permissionStore``
// (fed by the authoritative ``/api/permissions/pending`` poll AND the
// focused task's live SSE ``control_request`` — see the store). Because
// the store polls the server truth, the dialog surfaces no matter which
// task is in view and even when the per-task SSE frame never arrived —
// closing the "I had to refresh the page to see the popup" bug.
//
// Remembered "Allow always" / "Deny always" decisions are resolved
// SERVER-SIDE before an ask ever reaches this store (see
// kato_core_lib/helpers/tool_decision_store.py) — clicking "remember"
// here just tells the backend to persist the choice via the ``remember``
// flag on the submit call. When the resolved ask belongs to a task whose
// chat is mounted, the audit bubble is routed back into that chat
// through the store's audit-sink registry.
export default function GlobalPermissionContainer() {
  const { list } = usePendingPermissions();
  // Hold the dialog back while the operator is mid-sentence somewhere.
  //
  // It used to appear over whatever they were writing WITHOUT moving
  // focus, so the next Enter — meant to send that message — approved a
  // request they had never read and submitted the half-written prompt.
  // Fixing the keystroke alone was not enough: a dialog that covers the
  // screen the instant you start a sentence is the wrong behaviour even
  // when the keys go to the right place. So it waits for a pause, and
  // says it is waiting rather than sitting invisible.
  const typing = useOperatorIsTyping();
  // Oldest ask first (store preserves insertion order).
  const current = list[0] || null;
  const currentTaskId = current ? unpackPermissionEnvelope(current).taskId : '';
  const currentRequestId = current ? unpackPermissionEnvelope(current).requestId : '';

  const submit = useCallback(async ({ requestId, allow, rationale, remember }) => {
    if (!currentTaskId) { return false; }
    const result = await postSession(currentTaskId, 'permission', {
      request_id: requestId,
      allow,
      rationale,
      remember: !!remember,
    });
    if (result.ok) {
      // Resolve immediately so the modal closes without waiting for the
      // next poll; the tombstone stops a racing poll from re-opening it.
      permissionStore.resolve(requestId);
    }
    return !!result.ok;
  }, [currentTaskId]);

  const dismiss = useCallback(() => {
    if (currentRequestId) { permissionStore.resolve(currentRequestId); }
  }, [currentRequestId]);

  const auditBubble = useCallback((bubble) => {
    // Route the "✓ approved / ✗ denied" bubble into the asking task's chat
    // if it's mounted (focused task); a no-op otherwise (background task).
    permissionStore.emitAudit(currentTaskId, bubble);
  }, [currentTaskId]);

  if (!current) { return null; }
  if (typing) {
    return (
      <div className="permission-pending-hint" role="status">
        <span className="permission-pending-dot" aria-hidden="true" />
        Waiting for your approval — finish typing and it will open.
      </div>
    );
  }

  return (
    <PermissionDecisionContainer
      // Remount only when the actual task+request changes — a fresh poll
      // object for the SAME ask must not tear down a modal mid-decision.
      key={`${currentTaskId}:${currentRequestId}`}
      pending={current}
      onDismiss={dismiss}
      onSubmit={submit}
      onAuditBubble={auditBubble}
      taskCode={currentTaskId}
      taskSummary={unpackPermissionEnvelope(current).taskSummary}
    />
  );
}

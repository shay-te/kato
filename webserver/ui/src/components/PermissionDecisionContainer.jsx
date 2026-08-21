import { useState } from 'react';
import PermissionModal from './PermissionModal.jsx';
import { unpackPermissionEnvelope } from '../utils/permissionEnvelope.js';

// Renders the permission modal for whatever ``pending`` ask the backend
// hands it. Remembered "Allow always" / "Deny always" decisions are
// backend-owned (see kato_core_lib/helpers/tool_decision_store.py) — the
// server auto-resolves a matching request BEFORE it is ever surfaced here
// (the pending-list poll, the SSE stream, and the tab-attention feed all
// check first), so ``pending`` only ever contains an ask that genuinely
// needs a human. This component has no recall/auto-submit logic of its
// own to keep in sync with that.
export default function PermissionDecisionContainer({
  pending,
  onDismiss,
  onSubmit,
  onAuditBubble,
  taskCode = '',
  taskSummary = '',
  queuedCount = 0,
}) {
  const [submittingRequestId, setSubmittingRequestId] = useState('');

  if (!pending) { return null; }
  const { requestId: pendingRequestId } = unpackPermissionEnvelope(pending);
  if (submittingRequestId && submittingRequestId === pendingRequestId) { return null; }

  async function handleDecide(decision) {
    const { allow, rationale, remember, requestId, toolName } = decision;
    setSubmittingRequestId(requestId);
    const delivered = await deliverDecision(onSubmit, {
      requestId,
      allow,
      rationale,
      remember,
    });
    setSubmittingRequestId('');
    if (!delivered) { return; }
    onDismiss();
    const verb = allow ? '✓ approved' : '✗ denied';
    const memorySuffix = remember && toolName ? ` (remembered for ${toolName})` : '';
    onAuditBubble({
      kind: 'system',
      text: `${verb} permission ${requestId}${memorySuffix}`,
    });
  }

  return (
    <PermissionModal
      raw={pending}
      onDecide={handleDecide}
      taskCode={taskCode}
      taskSummary={taskSummary}
      queuedCount={queuedCount}
    />
  );
}

async function deliverDecision(onSubmit, decision) {
  try {
    return await onSubmit(decision);
  } catch (_) {
    return false;
  }
}

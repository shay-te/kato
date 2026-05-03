import { useEffect, useState } from 'react';
import PermissionModal from './PermissionModal.jsx';
import { unpackPermissionEnvelope } from '../utils/permissionEnvelope.js';

export default function PermissionDecisionContainer({
  pending,
  onDismiss,
  onSubmit,
  onAuditBubble,
  recallToolDecision,
  rememberToolDecision,
}) {
  const [submittingRequestId, setSubmittingRequestId] = useState('');
  const [autoFailedRequestId, setAutoFailedRequestId] = useState('');

  useEffect(() => {
    if (!pending) { return; }
    const { toolName, requestId } = unpackPermissionEnvelope(pending);
    if (!requestId || requestId === autoFailedRequestId) { return; }
    const remembered = recallToolDecision(toolName);
    if (!remembered) { return; }
    const allow = remembered === 'allow';
    let cancelled = false;
    setSubmittingRequestId(requestId);
    async function submitRememberedDecision() {
      const delivered = await deliverDecision(onSubmit, {
        requestId,
        allow,
        rationale: '',
        remember: false,
      });
      if (cancelled) { return; }
      setSubmittingRequestId('');
      if (!delivered) {
        setAutoFailedRequestId(requestId);
        return;
      }
      onDismiss();
      onAuditBubble({
        kind: 'system',
        text: `(auto-${allow ? 'allow' : 'deny'}ed for ${toolName} — remembered for this session)`,
      });
    }
    submitRememberedDecision();
    return () => { cancelled = true; };
  }, [
    pending,
    recallToolDecision,
    onDismiss,
    onSubmit,
    onAuditBubble,
    autoFailedRequestId,
  ]);

  if (!pending) { return null; }
  const { toolName: pendingTool, requestId: pendingRequestId } = unpackPermissionEnvelope(pending);
  const autoSubmitting = submittingRequestId && submittingRequestId === pendingRequestId;
  const remembered = recallToolDecision(pendingTool);
  const hideRemembered = remembered && pendingRequestId !== autoFailedRequestId;
  if (autoSubmitting || hideRemembered) { return null; }

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
    if (remember) { rememberToolDecision(toolName, allow); }
    onDismiss();
    setAutoFailedRequestId('');
    const verb = allow ? '✓ approved' : '✗ denied';
    const memorySuffix = remember && toolName ? ` (remembered for ${toolName})` : '';
    onAuditBubble({
      kind: 'system',
      text: `${verb} permission ${requestId}${memorySuffix}`,
    });
  }

  return (
    <PermissionModal raw={pending} onDecide={handleDecide} />
  );
}

async function deliverDecision(onSubmit, decision) {
  try {
    return await onSubmit(decision);
  } catch (_) {
    return false;
  }
}

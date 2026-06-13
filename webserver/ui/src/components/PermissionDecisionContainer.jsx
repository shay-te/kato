import { useEffect, useState } from 'react';
import PermissionModal from './PermissionModal.jsx';
import {
  unpackPermissionEnvelope,
  decisionCommandFor,
} from '../utils/permissionEnvelope.js';

export default function PermissionDecisionContainer({
  pending,
  onDismiss,
  onSubmit,
  onAuditBubble,
  recallToolDecision,
  rememberToolDecision,
  taskCode = '',
  taskSummary = '',
}) {
  const [submittingRequestId, setSubmittingRequestId] = useState('');
  const [autoFailedRequestId, setAutoFailedRequestId] = useState('');

  useEffect(() => {
    if (!pending) { return; }
    const {
      toolName, toolInput, requestId, outsideSandbox,
    } = unpackPermissionEnvelope(pending);
    if (!requestId || requestId === autoFailedRequestId) { return; }
    // Out-of-task asks never auto-resolve from a remembered decision — force
    // the modal so the operator decides each one explicitly.
    if (outsideSandbox) { return; }
    // Command-keyed tools (Bash) recall by the command's PROGRAM, so a
    // remembered `mvn` auto-resolves any future `mvn …` but never a `docker` ask.
    const remembered = recallToolDecision(
      toolName, decisionCommandFor(toolName, toolInput),
    );
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
        text: `(auto-${allow ? 'allow' : 'deny'}ed for ${toolName} — remembered across kato restarts)`,
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
  const {
    toolName: pendingTool, toolInput: pendingInput,
    requestId: pendingRequestId, outsideSandbox: pendingOutside,
  } = unpackPermissionEnvelope(pending);
  const autoSubmitting = submittingRequestId && submittingRequestId === pendingRequestId;
  const remembered = recallToolDecision(
    pendingTool, decisionCommandFor(pendingTool, pendingInput),
  );
  // Out-of-task asks are never hidden behind a remembered decision — they
  // always surface the modal (see the auto-resolve guard above).
  const hideRemembered = remembered && !pendingOutside
    && pendingRequestId !== autoFailedRequestId;
  if (autoSubmitting || hideRemembered) { return null; }

  async function handleDecide(decision) {
    const { allow, rationale, remember, requestId, toolName, command } = decision;
    setSubmittingRequestId(requestId);
    const delivered = await deliverDecision(onSubmit, {
      requestId,
      allow,
      rationale,
      remember,
    });
    setSubmittingRequestId('');
    if (!delivered) { return; }
    // Command-keyed tools remember the command's program signature (from the modal).
    if (remember) { rememberToolDecision(toolName, allow, command || ''); }
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
    <PermissionModal
      raw={pending}
      onDecide={handleDecide}
      taskCode={taskCode}
      taskSummary={taskSummary}
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

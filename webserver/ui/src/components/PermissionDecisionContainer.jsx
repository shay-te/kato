import { useEffect, useState } from 'react';
import PermissionModal from './PermissionModal.jsx';
import {
  unpackPermissionEnvelope,
  isExecutionTool,
} from '../utils/permissionEnvelope.js';

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
    const { toolName, requestId, outsideSandbox } = unpackPermissionEnvelope(pending);
    if (!requestId || requestId === autoFailedRequestId) { return; }
    // High-risk asks never auto-resolve from a remembered decision — force
    // the modal (with its loud warning) so the operator decides each one:
    //   * out-of-sandbox: a tool-name "allow" would otherwise approve an
    //     out-of-folder ask too;
    //   * execution (Bash/Monitor): kato must never silently run software
    //     (docker, build scripts) on a past "allow always".
    if (outsideSandbox || isExecutionTool(toolName)) { return; }
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
    toolName: pendingTool, requestId: pendingRequestId, outsideSandbox: pendingOutside,
  } = unpackPermissionEnvelope(pending);
  const autoSubmitting = submittingRequestId && submittingRequestId === pendingRequestId;
  const remembered = recallToolDecision(pendingTool);
  // High-risk asks (out-of-sandbox / execution) are never hidden behind a
  // remembered decision — they always surface the modal (see the
  // auto-resolve guard above).
  const highRisk = pendingOutside || isExecutionTool(pendingTool);
  const hideRemembered = remembered && !highRisk
    && pendingRequestId !== autoFailedRequestId;
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

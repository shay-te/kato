import { useEffect } from 'react';
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
  useEffect(() => {
    if (!pending) { return; }
    const { toolName, requestId } = unpackPermissionEnvelope(pending);
    const remembered = recallToolDecision(toolName);
    if (!remembered) { return; }
    const allow = remembered === 'allow';
    onDismiss();
    onSubmit({
      requestId,
      allow,
      rationale: '',
      remember: false,
    });
    onAuditBubble({
      kind: 'system',
      text: `(auto-${allow ? 'allow' : 'deny'}ed for ${toolName} — remembered for this session)`,
    });
  }, [pending, recallToolDecision, onDismiss, onSubmit, onAuditBubble]);

  if (!pending) { return null; }
  const { toolName: pendingTool } = unpackPermissionEnvelope(pending);
  if (recallToolDecision(pendingTool)) { return null; }

  function handleDecide(decision) {
    const { allow, rationale, remember, requestId, toolName } = decision;
    if (remember) { rememberToolDecision(toolName, allow); }
    onDismiss();
    onSubmit({ requestId, allow, rationale, remember });
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

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
  inline = false,
  timedGrantOutsideWorkspace = false,
  // Stops the agent's subprocess. Wired through from the container that owns
  // the task id; the plan dialog offers it as the third decision.
  onStop = null,
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
      // Approve and deny are opposite outcomes recorded in the same kind of
      // bubble, so without a tone they were the same colour and the ✓ / ✗
      // glyph was the only difference — easy to misread when scrolling back
      // through a long run to check what was actually allowed.
      tone: allow ? 'is-approved' : 'is-denied',
      text: `${verb} permission ${requestId}${memorySuffix}`,
    });
  }

  // Answer the ask FIRST, then stop. The agent is blocked on this request, and
  // an unanswered ask left behind by a stop reads in the transcript as a
  // decision nobody made.
  async function handleStop(decision) {
    await handleDecide({
      ...decision,
      allow: false,
      remember: false,
      rationale: 'The user stopped the agent instead of approving the plan.',
    });
    if (typeof onStop !== 'function') { return; }
    const stopped = await onStop();
    onAuditBubble({
      kind: 'system',
      tone: 'is-denied',
      text: stopped
        ? '■ stopped the agent — your next message resumes the session'
        : '✗ stop failed — the agent may still be running',
    });
  }

  return (
    <PermissionModal
      raw={pending}
      onDecide={handleDecide}
      onStop={onStop ? handleStop : null}
      taskCode={taskCode}
      taskSummary={taskSummary}
      queuedCount={queuedCount}
      inline={inline}
      timedGrantOutsideWorkspace={timedGrantOutsideWorkspace}
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

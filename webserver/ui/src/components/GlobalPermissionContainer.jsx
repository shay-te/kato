import { useCallback, useEffect, useRef } from 'react';
import { useOperatorIsTyping } from '../hooks/useOperatorIsTyping.js';
import { useTitleAlert } from '../hooks/useTitleAlert.js';
import PermissionDecisionContainer from './PermissionDecisionContainer.jsx';
import { postSession } from '../api.js';
import { permissionStore } from '../stores/permissionStore.js';
import { usePendingPermissions } from '../hooks/usePendingPermissions.js';
import { unpackPermissionEnvelope } from '../utils/permissionEnvelope.js';

// The SINGLE owner of the permission-approval modal.
//
// It still WATCHES every task — that part is deliberate and load-bearing —
// but it only OPENS for the task the operator is currently on.
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
//
// WATCHING every task vs OPENING for one is the distinction that matters.
// The store is global on purpose: it polls the server's own list, so an ask
// surfaces even when the per-task SSE frame never arrived — that is what
// fixed "I had to refresh the page to see the popup", and narrowing the
// STORE would bring it straight back.
//
// The DIALOG is a different question. A modal is a demand for attention
// right now, and one raised by a task the operator is not looking at
// interrupts whatever they are actually doing — reported as "it blocks my
// flow while I am working on another task". So a background task's ask stays
// in the store and keeps its non-blocking signals (the tab badge via
// ``mergePendingPermissionTaskIds``, the flashing title, the desktop
// notification) and opens the moment the operator switches to that task.
//
// The agent is not forgotten either way: it stays blocked until answered,
// which is exactly why the quiet signals have to keep firing for tasks that
// are NOT on screen.
export default function GlobalPermissionContainer({ activeTaskId = '' }) {
  const { list } = usePendingPermissions();
  // Asks belonging to the task on screen. The dialog is drawn from THIS;
  // everything below that reports on "waiting" still reads the full list.
  const mine = list.filter(
    (entry) => unpackPermissionEnvelope(entry).taskId === activeTaskId,
  );
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
  // Whichever ask is ALREADY on screen stays on screen until it is answered;
  // everything else queues behind it. Without this the dialog re-picked the
  // list's head on every poll, so a second ask arriving mid-decision replaced
  // the one being answered — and an AskUserQuestion form the operator had
  // half filled in was torn down with every radio button and typed word in it.
  // (The store rebuilds its map from the server's list, so even the ORDER of
  // two already-pending asks can flip under a running dialog.)
  const shownRequestIdRef = useRef('');
  //
  // Scoped to ``mine``, so switching tasks releases a held dialog rather
  // than dragging the previous task's ask along to the new one.
  const held = shownRequestIdRef.current
    ? mine.find((entry) => (
      unpackPermissionEnvelope(entry).requestId === shownRequestIdRef.current
    )) || null
    : null;
  // Oldest ask first (store preserves insertion order) unless one is held.
  const current = held || mine[0] || null;
  // Flash the browser tab title while anything is waiting. The desktop
  // notification already fired, but notifications get missed — and an
  // agent sits blocked for exactly as long as nobody notices, so the cost
  // of a missed one is wall-clock time. Only flashes while the tab is in
  // the background; with kato in front the dialog is already on screen.
  useTitleAlert(
    list.length > 0,
    list.length > 1
      ? `(${list.length}) Approval needed — kato`
      : 'Approval needed — kato',
  );
  const currentTaskId = current ? unpackPermissionEnvelope(current).taskId : '';
  const currentRequestId = current ? unpackPermissionEnvelope(current).requestId : '';
  // The typing gate only decides whether an ask may OPEN over what the
  // operator is writing. Once it is open it stays open — including while
  // they type into the dialog's own fields.
  const open = !!current && (!typing || !!held);
  useEffect(() => {
    shownRequestIdRef.current = open ? currentRequestId : '';
  }, [open, currentRequestId]);

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
  if (!open) {
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
      // Held-back asks are invisible until this one is answered, and an
      // agent stays blocked for as long as nobody knows it is waiting —
      // so the dialog says how many are queued behind it.
      queuedCount={Math.max(0, mine.length - 1)}
    />
  );
}

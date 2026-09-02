import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useOperatorIsTyping } from '../hooks/useOperatorIsTyping.js';
import { useTitleAlert } from '../hooks/useTitleAlert.js';
import PermissionDecisionContainer from './PermissionDecisionContainer.jsx';
import { postSession } from '../api.js';
import { permissionStore } from '../stores/permissionStore.js';
import { usePendingPermissions } from '../hooks/usePendingPermissions.js';
import { unpackPermissionEnvelope } from '../utils/permissionEnvelope.js';
import { countNoun } from '../utils/pluralize.js';
import {
  APPROVAL_MODE_GLOBAL,
  readApprovalMode,
  subscribeApprovalMode,
} from '../utils/approvalModePref.js';

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
// How many waiting tasks get their own pill before the rest collapse into a
// count. Two fits the header at a laptop width without crowding the scan
// status beside it; the overflow keeps its ids in a tooltip.
const ROSTER_VISIBLE_CHIPS = 2;

export default function GlobalPermissionContainer({
  activeTaskId = '',
  onSelectTask = null,
}) {
  const { list } = usePendingPermissions();
  // Where the ask is drawn. A setting, because neither answer is right for
  // everyone: an interrupting dialog costs you your place in another task,
  // and a quiet in-chat card costs a blocked agent some of your attention.
  // See utils/approvalModePref.js.
  const [approvalMode, setApprovalMode] = useState(() => readApprovalMode());
  useEffect(() => subscribeApprovalMode(setApprovalMode), []);
  const globalMode = approvalMode === APPROVAL_MODE_GLOBAL;
  // Asks belonging to the task on screen. The dialog is drawn from THIS;
  // everything below that reports on "waiting" still reads the full list.
  // In global mode EVERY task's ask is eligible, wherever the operator is —
  // that is the whole difference between the two modes. The store itself
  // watches every task either way, so nothing is missed in either.
  const mine = globalMode ? list : list.filter(
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
  // Re-resolves the slot when an ask appears: the chat pane can mount its
  // slot at any time relative to this component, and the ask may arrive long
  // after both have settled.
  const hasAsk = !!current;
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

  // Every task with someone waiting, oldest first, one row each. This is the
  // roster: it is how a task that is NOT on screen says it needs an answer,
  // and it is deliberately not a dialog — nothing here interrupts, and
  // picking a row is what takes the operator to the ask.
  const waiting = [];
  const seenTasks = new Set();
  for (const entry of list) {
    const { taskId, taskSummary } = unpackPermissionEnvelope(entry);
    if (!taskId || seenTasks.has(taskId)) { continue; }
    seenTasks.add(taskId);
    waiting.push({ taskId, taskSummary });
  }

  const roster = waiting.length > 0 ? (
    <span
      className="permission-roster"
      role="status"
      aria-live="polite"
      // The visible pills are task ids, which say nothing on their own to a
      // screen reader. The count and the point of the thing live here.
      aria-label={`${countNoun(waiting.length, 'chat')} waiting for you`}
    >
      {/* At most two, then a count. This sits in the app header beside the
          logo and the scan status — a row that is already busy and cannot
          grow. An uppercase "WAITING FOR YOU" banner plus one pill per task
          overran it and the pills collided with the status text. */}
      {waiting.slice(0, ROSTER_VISIBLE_CHIPS).map((row) => (
        <button
          key={row.taskId}
          type="button"
          className={`permission-roster-chip${
            row.taskId === activeTaskId ? ' is-active' : ''}`}
          // Opens the task, rather than only naming it. Being told which chat
          // is blocked and then having to find it among the tabs is most of
          // the work; this is the point of the control.
          onClick={() => onSelectTask && onSelectTask(row.taskId)}
          title={
            row.taskId === activeTaskId
              ? `${row.taskSummary || row.taskId} is waiting for you — you are here`
              : `${row.taskId} is waiting for you. Click to open it.${
                row.taskSummary ? ` — ${row.taskSummary}` : ''}`
          }
        >
          <span className="permission-roster-dot" aria-hidden="true" />
          {row.taskId}
        </button>
      ))}
      {waiting.length > ROSTER_VISIBLE_CHIPS ? (
        <span
          className="permission-roster-more"
          title={waiting.slice(ROSTER_VISIBLE_CHIPS)
            .map((row) => row.taskId).join(', ')}
        >
          {`+${waiting.length - ROSTER_VISIBLE_CHIPS}`}
        </span>
      ) : null}
    </span>
  ) : null;

  // Lives in the header, beside the logo — the one strip that is on screen
  // whatever else the operator is doing. An agent stays blocked until it is
  // answered, so the notice has to be somewhere that is never scrolled past
  // or covered by a pane.
  const headerSlot = typeof document !== 'undefined'
    ? document.getElementById('header-attention-slot')
    : null;

  // The ask itself, rendered INSIDE the task's own chat rather than over the
  // whole app. Portaled into the slot SessionDetail renders between the
  // transcript and the composer, so this component stays the single owner of
  // the submit/resolve path instead of that logic being copied into the chat.
  //
  // The slot only exists while that task's chat is mounted, which is exactly
  // the condition for showing the ask at all.
  // Resolved in an effect, not during render: the slot belongs to the chat
  // pane, which mounts AFTER this component, so a render-time lookup finds
  // nothing on the first pass and the ask never appears. Re-run per task,
  // because switching tasks unmounts the old slot and mounts a new one.
  const [cachedSlot, setCachedSlot] = useState(null);
  useEffect(() => {
    setCachedSlot(
      (typeof document !== 'undefined'
        && document.getElementById('chat-permission-slot')) || null,
    );
  }, [activeTaskId, hasAsk]);
  // Never portal into a DETACHED node.
  //
  // Caching the element is what makes the first render work, but a cached
  // node outlives the DOM it came from: anything that unmounts and remounts
  // the chat pane without changing the deps above leaves this pointing at an
  // element no longer in the document, and the ask renders into nothing
  // while the agent is still blocked waiting for the answer. The
  // ``isConnected`` check plus a live re-read costs one DOM lookup and makes
  // that state unreachable rather than merely unlikely.
  const slot = cachedSlot && cachedSlot.isConnected
    ? cachedSlot
    : ((typeof document !== 'undefined'
      && document.getElementById('chat-permission-slot')) || null);

  const card = current && open ? (
    <PermissionDecisionContainer
      // Remount only when the actual task+request changes — a fresh poll
      // object for the SAME ask must not tear down a half-filled answer.
      key={`${currentTaskId}:${currentRequestId}`}
      pending={current}
      onDismiss={dismiss}
      onSubmit={submit}
      onAuditBubble={auditBubble}
      taskCode={currentTaskId}
      taskSummary={unpackPermissionEnvelope(current).taskSummary}
      // Only this task's other asks. One on a DIFFERENT task is not queued
      // behind this dialog — it waits on its own row in the roster above.
      queuedCount={Math.max(0, mine.length - 1)}
      // Global mode is a real modal: an overlay, not a card in a transcript.
      inline={!globalMode}
    />
  ) : null;

  const typingHint = current && !open ? (
    <div className="permission-pending-hint" role="status">
      <span className="permission-pending-dot" aria-hidden="true" />
      Waiting for your approval — finish typing and it will open.
    </div>
  ) : null;

  return (
    <>
      {headerSlot && roster ? createPortal(roster, headerSlot) : roster}
      {/* The overlay is rendered HERE, not portaled into a chat: in global
          mode it deliberately belongs to the whole app rather than to one
          task's pane — and it must still appear when the asking task's chat
          is not mounted at all, which is exactly the case the mode exists
          for. */}
      {globalMode
        ? <>{card}{typingHint}</>
        : (slot && (card || typingHint)
          ? createPortal(<>{card}{typingHint}</>, slot)
          : null)}
    </>
  );
}

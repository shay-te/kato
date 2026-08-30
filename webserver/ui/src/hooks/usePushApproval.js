import { useEffect, useState } from 'react';
import { approveTaskPush } from '../api.js';
import { useBusyAction } from './useBusyAction.js';

// "Kato is paused waiting for you to approve the push" — read from the session
// record, not from a poll of its own.
//
// There used to be a dedicated 5s poll of
// ``GET /api/sessions/<id>/awaiting-push-approval`` here. That endpoint and
// the ``has_changes_pending`` field on the 5s-polled session record are the
// SAME server expression (app.py binds
// ``agent_service.publish.is_awaiting_push_approval`` in both places), and
// SessionHeader already receives the record — the tab row and the forget
// dialog have been reading the field all along.
//
// So it was not just 12 requests a minute for a value already on the wire: it
// was TWO unsynchronised 5s timers over ONE boolean, which is a correctness
// problem rather than a cost one. The Approve-push button and the tab's
// pending-changes row could disagree for up to five seconds, and which one was
// right depended on which timer had fired more recently. One source cannot
// disagree with itself.
export function usePushApproval(session) {
  const taskId = session?.task_id || '';
  const reported = !!session?.has_changes_pending;

  // Approving clears the button NOW; the record catches up on the next
  // /api/sessions tick. Without this the operator clicks Approve and the
  // button sits there for up to 5 seconds looking like the click missed.
  const [dismissed, setDismissed] = useState(false);

  // Drop the optimistic clear when the task changes (a different task's
  // approval says nothing about this one) and on ANY change to the server's
  // flag — in either direction.
  //
  // The direction matters. Guarding this on ``!reported`` looks equivalent and
  // is not: ``approve_push`` pops the pending entry BEFORE running the push
  // the HTTP request is still blocked on (task_publish_service.py), and the
  // 5s session poll is served mid-publish. So the flag can go false, and then
  // true again when the task re-parks, while ``dismissed`` is being set true
  // by a POST that only just resolved. Edge-triggered on the falling edge
  // only, the clear never ran again and the Approve button was gone for the
  // rest of the tab's life — with the tab row still reporting unpushed work
  // and no control left to resume the parked publish.
  useEffect(() => { setDismissed(false); }, [taskId]);
  useEffect(() => { setDismissed(false); }, [reported]);

  const [busy, approve] = useBusyAction(
    () => approveTaskPush(taskId),
    {
      enabled: !!taskId,
      // Only on success. A failed approve must leave the button up — hiding it
      // would strand the operator with no way to retry and no sign why.
      onDone: (result) => { if (result.ok) { setDismissed(true); } },
    },
  );

  return { awaiting: reported && !dismissed, busy, approve };
}

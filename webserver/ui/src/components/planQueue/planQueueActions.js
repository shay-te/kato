// Talking to the plan queue routes: one place that turns a refusal into a toast.
//
// Every route answers with an envelope; a refusal carries the queue's own
// reason ("the plan is running; this needs it proposed"), shown as one red
// toast. ``makeCall`` is a thunk so a busy button never fires a request.

import { toast } from '../../stores/toastStore.js';

export async function planAction(makeCall, failureTitle) {
  const result = await makeCall();
  if (!result?.ok) {
    toast.errorFromResult(result, { title: failureTitle });
    return null;
  }
  return result.body || {};
}

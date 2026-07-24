// Classification kinds emitted by classifyStatusEntry — drive notification
// routing and tab attention marking. Distinct from TAB_STATUS even when the
// strings happen to match (e.g. 'attention'): one classifies log messages,
// the other paints the dot.

export const NOTIFICATION_KIND = Object.freeze({
  STARTED: 'started',
  STATUS_CHANGE: 'status_change',
  COMPLETED: 'completed',
  ATTENTION: 'attention',
  ERROR: 'error',
  REPLY: 'reply',
  // "Update source" (push + shift the operator's local clones to the task
  // branch) finished. It can take a while for a big / multi-repo task, so an
  // OS notification lets the operator step away and be pinged when it's done.
  SOURCE_UPDATE: 'source_update',
});

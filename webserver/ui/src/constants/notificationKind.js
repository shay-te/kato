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
});

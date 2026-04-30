// Tab dot statuses. Values mirror the server-side WORKSPACE_STATUS_* enum
// in kato/data_layers/service/workspace_manager.py — keep in sync.
//
// `ATTENTION` is UI-only: it's overlaid on top of any base status when a
// tab has a pending permission or other "needs your input" signal.

export const TAB_STATUS = Object.freeze({
  PROVISIONING: 'provisioning',
  ACTIVE: 'active',
  IDLE: 'idle',
  REVIEW: 'review',
  DONE: 'done',
  TERMINATED: 'terminated',
  ERRORED: 'errored',
  ATTENTION: 'attention',
});

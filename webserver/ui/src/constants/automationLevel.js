// How far kato takes a task by itself — the ``automation`` field on a tab in the
// tab list. Mirrors AUTOPILOT / AUTO_WAIT in
// kato_core_lib/helpers/autopilot_store.py (tests/test_auto_wait.py pins them equal).
//
// AUTOPILOT (``kato:autopilot``): the work, its review loop if tagged, and Done
// pressed by kato. AUTO_WAIT (``kato:auto-wait``): the work and the review loop,
// then the operator's own Done — its rail row has a green border.

export const AUTOMATION_LEVEL = Object.freeze({
  AUTOPILOT: 'autopilot',
  AUTO_WAIT: 'auto-wait',
});

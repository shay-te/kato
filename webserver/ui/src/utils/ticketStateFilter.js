// The tasks menu's status filter: every task, or only those whose ticket is in
// one state (YouTrack / Jira, or kato's own local tasks) — "add a dropdown to
// select YouTrack/Jira statuses, and all, to filter what tasks to show, so we
// know where they are" (2026-11-18). Each task row carries ``ticket_state``
// (the server's ``TicketStates``: '' = not known, or a tracker that cannot say).
//
// Pure functions: what the dropdown offers, and which tasks it keeps.

export const ALL_TICKET_STATES = '';
// The option for tasks with no known state.
export const NO_TICKET_STATE = '(no status)';

export function ticketStateOf(session) {
  return String((session && session.ticket_state) || '').trim();
}

// ``[{ value, label, count }]``: "All" first, then each state the tasks are in
// (alphabetical, so the list does not jump as counts change), then "No
// status". The state picked stays offered at 0 when no task is in it any more,
// so the dropdown still shows what it is filtering by.
export function ticketStateOptions(sessions, picked = ALL_TICKET_STATES) {
  const list = Array.isArray(sessions) ? sessions : [];
  const counts = new Map();
  for (const session of list) {
    const state = ticketStateOf(session) || NO_TICKET_STATE;
    counts.set(state, (counts.get(state) || 0) + 1);
  }
  if (picked && !counts.has(picked)) { counts.set(picked, 0); }
  const states = [...counts.keys()].filter((state) => state !== NO_TICKET_STATE).sort((a, b) => a.localeCompare(b));
  return [
    { value: ALL_TICKET_STATES, label: 'All', count: list.length },
    ...states.map((state) => ({ value: state, label: state, count: counts.get(state) })),
    ...(counts.has(NO_TICKET_STATE)
      ? [{ value: NO_TICKET_STATE, label: 'No status', count: counts.get(NO_TICKET_STATE) }]
      : []),
  ];
}

// Worth a dropdown only once some task's state is known (or one is picked).
export function hasTicketStates(sessions, picked = ALL_TICKET_STATES) {
  return !!picked || (Array.isArray(sessions) && sessions.some((session) => ticketStateOf(session)));
}

// The tasks in ``picked`` — all of them for "All". ``keep``: the task on
// screen stays whatever its state, so the menu always shows where you are.
export function filterByTicketState(sessions, picked, { keep = '' } = {}) {
  const list = Array.isArray(sessions) ? sessions : [];
  if (!picked) { return list; }
  return list.filter((session) => (
    (keep && session.task_id === keep) || (ticketStateOf(session) || NO_TICKET_STATE) === picked
  ));
}

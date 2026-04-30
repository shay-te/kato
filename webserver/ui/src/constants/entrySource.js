// Where an event-log entry came from. Drives EventLog rendering rules
// (e.g. only `LOCAL` entries carry their own pre-formatted text; `HISTORY`
// entries should not flip turnInFlight in the reducer).

export const ENTRY_SOURCE = Object.freeze({
  LOCAL: 'local',
  SERVER: 'server',
  HISTORY: 'history',
});

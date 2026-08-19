// Search model for the task palette (Ctrl/Cmd+P).
//
// Pure functions only — no React, no DOM — so the ranking rules are
// testable without jsdom and the component stays about presentation.

import { fuzzyMatches, fuzzyRank } from './fuzzyMatch.js';

// How many rows the palette will render. A palette is for finding one
// task, not for browsing all of them: past a screenful the list stops
// helping and the operator should type another character instead.
export const TASK_PALETTE_LIMIT = 50;

// The searchable text for one session. The id is what an operator
// actually remembers ("UNA-2818"), the summary is what they remember
// when they don't remember the id, and the rename (``displayName``) is
// what they see on the tab — so all three have to match, or the palette
// fails on the exact term the operator is staring at.
function targetsFor(session, displayName) {
  return [session?.task_id, displayName, session?.task_summary];
}

// Filter + rank sessions for ``query``.
//
// ``nameFor(session) -> string`` supplies the operator's tab rename, so
// this module needs no knowledge of the rename store.
//
// Ranking, best first:
//   1. how well the TASK ID matches — an operator typing "una-28" wants
//      the ticket, and ids are what they type most;
//   2. then how well the display name matches;
//   3. then the original session order, so an empty query shows the
//      strip's own order rather than an arbitrary reshuffle.
//
// Stability matters: the first row is what Enter opens, so it must not
// jitter between keystrokes that score identically.
export function filterTaskPalette(sessions, query, nameFor, limit = TASK_PALETTE_LIMIT) {
  const list = Array.isArray(sessions) ? sessions : [];
  const name = typeof nameFor === 'function' ? nameFor : () => '';
  const term = String(query || '').trim();

  const rows = [];
  list.forEach((session, index) => {
    if (!session || !session.task_id) { return; }
    const displayName = String(name(session) || '');
    if (!fuzzyMatches(term, targetsFor(session, displayName))) { return; }
    rows.push({
      session,
      taskId: session.task_id,
      displayName: displayName || session.task_summary || session.task_id,
      summary: String(session.task_summary || ''),
      index,
    });
  });

  if (term) {
    rows.sort((a, b) => (
      fuzzyRank(term, a.taskId) - fuzzyRank(term, b.taskId)
      || fuzzyRank(term, a.displayName) - fuzzyRank(term, b.displayName)
      || a.index - b.index
    ));
  }
  return rows.slice(0, limit);
}

// Move the highlighted row by ``delta``, wrapping at both ends.
//
// Wrapping (rather than clamping) because the list is short and the
// operator is holding a key: stopping dead at the bottom reads as the
// palette having frozen.
export function nextPaletteIndex(current, delta, count) {
  if (!count || count <= 0) { return 0; }
  const from = Number.isInteger(current) ? current : 0;
  return ((from + delta) % count + count) % count;
}

import { useEffect, useState } from 'react';
import { createPubSub } from './pubsub.js';
import { reviewLoopSignature } from '../components/reviewLoop/reviewLoopHelpers.js';

// A review loop the operator just started or resumed, shown AT ONCE.
//
// Where a loop is comes from ``session.review_loop`` on the 5-second session
// poll, so Start changed nothing on screen for seconds: kato answered the
// click, then the view waited for the next poll. Now the click puts a
// "starting" loop here first, kato's answer replaces it, and ``useSessions``
// lays it over the polled list — so the pane, the header chip, the toolbar
// button and the task's row switch together, from the one place they all read.
//
// An entry ends once the poll has moved past what it showed at the click
// (``before``: that loop's signature), when the request fails, or after
// PENDING_REVIEW_LOOP_TTL_MS: it never stands in for the poll for long.

export const PENDING_REVIEW_LOOP_TTL_MS = 30000;

let entries = new Map(); // taskId -> { loop, before, at }
const pubsub = createPubSub(() => entries);

function publish(next) {
  entries = next;
  pubsub.emit();
}

function put(taskId, entry, timers) {
  const next = new Map(entries);
  next.set(taskId, entry);
  publish(next);
  // The safety net: drop it if the poll somehow never moves on.
  timers.setTimeout(() => {
    if (entries.get(taskId) === entry) { clearReviewLoopPending(taskId); }
  }, PENDING_REVIEW_LOOP_TTL_MS);
}

// ``loop`` shows for ``taskId`` until the poll's loop is no longer ``before``.
export function markReviewLoopPending(taskId, loop, before, { timers = globalThis } = {}) {
  if (!taskId || !loop) { return; }
  put(taskId, { loop, before: reviewLoopSignature(before) }, timers);
}

// kato's answer replaces the placeholder; the poll still has to catch up.
export function settleReviewLoopPending(taskId, loop, { timers = globalThis } = {}) {
  const entry = entries.get(taskId);
  if (!entry) { return; }
  if (!loop) {
    clearReviewLoopPending(taskId);
    return;
  }
  put(taskId, { ...entry, loop }, timers);
}

export function clearReviewLoopPending(taskId) {
  if (!entries.has(taskId)) { return; }
  const next = new Map(entries);
  next.delete(taskId);
  publish(next);
}

function aheadOfPoll(entry, session) {
  return reviewLoopSignature(session?.review_loop || null) === entry.before;
}

// ``sessions`` with each pending loop laid over its task's polled one — the
// same array when nothing is pending.
export function withPendingReviewLoops(sessions, pending) {
  if (!pending.size) { return sessions; }
  let changed = false;
  const shown = sessions.map((session) => {
    const entry = pending.get(session?.task_id);
    if (!entry || !aheadOfPoll(entry, session)) { return session; }
    changed = true;
    return { ...session, review_loop: entry.loop };
  });
  return changed ? shown : sessions;
}

// Drop the entries the poll has caught up with.
export function dropCaughtUpReviewLoops(sessions) {
  if (!entries.size) { return; }
  const byTask = new Map(sessions.map((session) => [session?.task_id, session]));
  const next = new Map(entries);
  for (const [taskId, entry] of entries) {
    if (byTask.has(taskId) && !aheadOfPoll(entry, byTask.get(taskId))) { next.delete(taskId); }
  }
  if (next.size !== entries.size) { publish(next); }
}

// Tests only: start each test with nothing pending.
export function _resetReviewLoopPending() {
  publish(new Map());
}

// taskId -> { loop, before }: what is pending right now.
export function readPendingReviewLoops() {
  return entries;
}

export function usePendingReviewLoops() {
  const [pending, setPending] = useState(readPendingReviewLoops);
  useEffect(() => pubsub.subscribe(setPending), []);
  return pending;
}

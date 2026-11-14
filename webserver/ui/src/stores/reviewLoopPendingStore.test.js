import { beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';

import {
  PENDING_REVIEW_LOOP_TTL_MS,
  _resetReviewLoopPending,
  clearReviewLoopPending,
  dropCaughtUpReviewLoops,
  markReviewLoopPending,
  readPendingReviewLoops,
  settleReviewLoopPending,
  withPendingReviewLoops,
} from './reviewLoopPendingStore.js';

// A clock the test moves by hand.
function fakeTimers() {
  let now = 0;
  let due = [];
  return {
    setTimeout(fn, ms) { due.push({ fn, at: now + ms }); return due.length; },
    advance(ms) {
      now += ms;
      const ready = due.filter((entry) => entry.at <= now);
      due = due.filter((entry) => entry.at > now);
      ready.forEach((entry) => entry.fn());
    },
  };
}

const finished = { loop_id: 'old', status: 'clean', phase: 'done', round: 2 };
const starting = { loop_id: '', status: 'running', phase: '', round: 1 };
const started = { loop_id: 'new', status: 'running', phase: 'reviewing', round: 1 };

function listed(loop) {
  return [{ task_id: 'UNA-1', review_loop: loop }, { task_id: 'UNA-2', review_loop: null }];
}

// What every surface reads: the polled list with what is pending laid over it.
function shown(sessions) {
  return withPendingReviewLoops(sessions, readPendingReviewLoops());
}

let timers;
beforeEach(() => {
  _resetReviewLoopPending();
  timers = fakeTimers();
});

test('nothing pending: the polled list itself, untouched', () => {
  const sessions = listed(finished);
  assert.equal(shown(sessions), sessions);
});

test('a click shows the starting loop on its task at once; the others are the same objects', () => {
  const sessions = listed(finished);
  markReviewLoopPending('UNA-1', starting, finished, { timers });
  const view = shown(sessions);
  assert.equal(view[0].review_loop, starting);
  assert.equal(view[0].task_id, 'UNA-1');
  assert.equal(view[1], sessions[1]);
  assert.equal(sessions[0].review_loop, finished, 'the polled list is never changed');
});

test('kato’s answer replaces the placeholder until the poll catches up', () => {
  markReviewLoopPending('UNA-1', starting, finished, { timers });
  settleReviewLoopPending('UNA-1', started, { timers });
  assert.equal(shown(listed(finished))[0].review_loop, started);

  // A poll from before kato started it still shows the old loop: keep showing ours.
  dropCaughtUpReviewLoops(listed(finished));
  assert.equal(readPendingReviewLoops().size, 1);

  // The poll moved on: it is the truth now, and the entry goes.
  const caughtUp = listed({ ...started, phase: 'verifying' });
  assert.equal(shown(caughtUp)[0].review_loop.phase, 'verifying');
  dropCaughtUpReviewLoops(caughtUp);
  assert.equal(readPendingReviewLoops().size, 0);
});

test('a resume of the SAME loop is caught up by its status, not its id', () => {
  const stopped = { loop_id: 'L', status: 'stopped', phase: 'done', round: 3 };
  markReviewLoopPending('UNA-1', { ...stopped, status: 'running', phase: '' }, stopped, { timers });
  assert.equal(shown(listed(stopped))[0].review_loop.status, 'running');
  const resumed = listed({ ...stopped, status: 'running', phase: 'awaiting_fix' });
  dropCaughtUpReviewLoops(resumed);
  assert.equal(readPendingReviewLoops().size, 0);
});

test('a task that never had a loop: pending until its first loop is polled', () => {
  markReviewLoopPending('UNA-1', starting, null, { timers });
  dropCaughtUpReviewLoops(listed(null));
  assert.equal(shown(listed(null))[0].review_loop, starting);
  dropCaughtUpReviewLoops(listed(started));
  assert.equal(readPendingReviewLoops().size, 0);
});

test('a refused or failed request takes the placeholder away', () => {
  markReviewLoopPending('UNA-1', starting, finished, { timers });
  clearReviewLoopPending('UNA-1');
  assert.equal(shown(listed(finished))[0].review_loop, finished);
  clearReviewLoopPending('UNA-1'); // nothing left: no-op

  markReviewLoopPending('UNA-1', starting, finished, { timers });
  settleReviewLoopPending('UNA-1', null, { timers }); // an answer with no loop in it
  assert.equal(readPendingReviewLoops().size, 0);
  settleReviewLoopPending('UNA-1', started, { timers }); // nothing pending: nothing to settle
  assert.equal(readPendingReviewLoops().size, 0);
});

test('nothing to mark without a task or a loop', () => {
  markReviewLoopPending('', starting, null, { timers });
  markReviewLoopPending('UNA-1', null, null, { timers });
  assert.equal(readPendingReviewLoops().size, 0);
  dropCaughtUpReviewLoops(listed(null)); // nothing pending: no-op
});

test('a task not in the polled list keeps its entry: only the clock ends it', () => {
  markReviewLoopPending('UNA-9', starting, null, { timers });
  dropCaughtUpReviewLoops(listed(finished));
  assert.equal(readPendingReviewLoops().size, 1);
  timers.advance(PENDING_REVIEW_LOOP_TTL_MS);
  assert.equal(readPendingReviewLoops().size, 0);
});

test('the clock is a safety net per entry: a later answer gets its own time', () => {
  markReviewLoopPending('UNA-1', starting, finished, { timers });
  timers.advance(PENDING_REVIEW_LOOP_TTL_MS - 1);
  settleReviewLoopPending('UNA-1', started, { timers });
  timers.advance(1); // the placeholder's time is up, the answer's is not
  assert.equal(readPendingReviewLoops().get('UNA-1').loop, started);
  timers.advance(PENDING_REVIEW_LOOP_TTL_MS);
  assert.equal(readPendingReviewLoops().size, 0);
});

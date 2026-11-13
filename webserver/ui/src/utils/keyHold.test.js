import assert from 'node:assert/strict';
import test from 'node:test';

import { createKeyHold } from './keyHold.js';

// A clock the test moves by hand.
function fakeTimers() {
  let now = 0;
  let next = 1;
  const pending = new Map();
  return {
    setTimeout(fn, ms) { const id = next++; pending.set(id, { fn, at: now + ms }); return id; },
    clearTimeout(id) { pending.delete(id); },
    advance(ms) {
      now += ms;
      for (const [id, { fn, at }] of [...pending]) {
        if (at <= now) { pending.delete(id); fn(); }
      }
    },
    get pending() { return pending.size; },
  };
}

function setup(thresholdMs = 300) {
  const timers = fakeTimers();
  const calls = [];
  const hold = createKeyHold({
    thresholdMs, timers, onHold: () => calls.push('hold'), onRelease: () => calls.push('release'),
  });
  return { timers, calls, hold };
}

test('a tap fires nothing and leaves no timer behind', () => {
  const { timers, calls, hold } = setup();
  hold.press('Tab');
  timers.advance(299);
  hold.keyUp('Tab');
  timers.advance(1000);
  assert.deepEqual(calls, []);
  assert.equal(hold.held, false);
  assert.equal(timers.pending, 0);
});

test('held past the threshold it reveals, and letting go hides', () => {
  const { timers, calls, hold } = setup();
  hold.press('Tab');
  timers.advance(300);
  assert.deepEqual(calls, ['hold']);
  assert.equal(hold.held, true);
  hold.keyUp('Tab');
  assert.deepEqual(calls, ['hold', 'release']);
  assert.equal(hold.held, false);
});

test('auto-repeat and taps under a held modifier continue the one hold', () => {
  const { timers, calls, hold } = setup();
  hold.press('Control');
  timers.advance(200);
  hold.press('Control');  // Tab tapped again, Ctrl still down
  timers.advance(100);
  assert.deepEqual(calls, ['hold']);  // timed from the first press
  hold.press('Control');
  hold.keyUp('Tab');  // not the key that ends it
  assert.deepEqual(calls, ['hold']);
  hold.keyUp('Control');
  assert.deepEqual(calls, ['hold', 'release']);
});

test('a lost window ends a hold; with no hold, cancel and stray keyups do nothing', () => {
  const { timers, calls, hold } = setup();
  hold.cancel();
  hold.keyUp('Tab');
  hold.press('Tab');
  timers.advance(400);
  hold.cancel();
  assert.deepEqual(calls, ['hold', 'release']);
  hold.press('');  // no key: nothing to wait for
  timers.advance(400);
  assert.deepEqual(calls, ['hold', 'release']);
});

test('a cancel before the threshold is a tap', () => {
  const { timers, calls, hold } = setup();
  hold.press('Tab');
  hold.cancel();
  timers.advance(1000);
  assert.deepEqual(calls, []);
});

test('each hold is its own: a second one reveals again', () => {
  const { timers, calls, hold } = setup(100);
  hold.press('Tab');
  timers.advance(100);
  hold.keyUp('Tab');
  hold.press('Tab');
  timers.advance(50);
  hold.keyUp('Tab');
  hold.press('Control');
  timers.advance(100);
  hold.keyUp('Control');
  assert.deepEqual(calls, ['hold', 'release', 'hold', 'release']);
});

test('the real timers are the default', async () => {
  const calls = [];
  const hold = createKeyHold({ thresholdMs: 5, onHold: () => calls.push('hold'), onRelease: () => {} });
  hold.press('Tab');
  await new Promise((done) => setTimeout(done, 30));
  assert.deepEqual(calls, ['hold']);
  hold.cancel();
});

import test from 'node:test';
import assert from 'node:assert/strict';

import { waitForScanToFinish } from './scanProgress.js';

// A controllable clock + sleep so the tests are instant and deterministic:
// ``sleep`` advances the fake clock instead of waiting.
function harness(statuses, { pickupMs = 4000, intervalMs = 600 } = {}) {
  let clock = 0;
  const queue = [...statuses];
  const calls = [];
  const fetchStatus = async () => {
    const next = queue.length > 1 ? queue.shift() : queue[0];
    calls.push(next);
    return next;
  };
  const sleep = async (ms) => { clock += ms; };
  const now = () => clock;
  return {
    calls,
    run: (overrides = {}) => waitForScanToFinish({
      fetchStatus, sleep, now, pickupMs, intervalMs, ...overrides,
    }),
  };
}

const SCANNING = { scanning: true, available: true };
const QUIET = { scanning: false, available: true };

test('waits through the pick-up window before the loop reports scanning', async () => {
  // The scan loop can be mid-sleep when the trigger lands, so the first polls
  // legitimately say "not scanning". Returning on the first false — which is
  // what ending the busy state with the POST effectively did — is the bug.
  const h = harness([QUIET, QUIET, SCANNING, SCANNING, QUIET]);
  assert.equal(await h.run(), 'finished');
  assert.ok(h.calls.length >= 5);
});

test('returns finished once a started scan goes quiet', async () => {
  const h = harness([SCANNING, SCANNING, QUIET]);
  assert.equal(await h.run(), 'finished');
});

test('gives up after the pick-up grace when the scan never starts', async () => {
  // Nothing picked the event up (or the scan finished inside the grace). The
  // button must be released either way — never left disabled.
  const h = harness([QUIET]);
  assert.equal(await h.run(), 'not-started');
});

test('stops immediately when no scan loop is wired', async () => {
  // Webserver-only boot / setup mode: nothing will ever report scanning, so
  // polling for it would spin the button until the timeout.
  const h = harness([{ scanning: false, available: false }]);
  assert.equal(await h.run(), 'unavailable');
  assert.equal(h.calls.length, 1);
});

test('a wedged scan is bounded by the timeout, not held forever', async () => {
  const h = harness([SCANNING]);
  assert.equal(await h.run({ timeoutMs: 5000 }), 'timeout');
});

test('cancels when the caller says stop (unmount)', async () => {
  const h = harness([SCANNING]);
  let ticks = 0;
  const result = await h.run({ shouldContinue: () => { ticks += 1; return ticks <= 3; } });
  assert.equal(result, 'cancelled');
});

test('no fetcher is not an error — it just reports unavailable', async () => {
  assert.equal(await waitForScanToFinish({}), 'unavailable');
});

// Wait for kato's scan to actually FINISH.
//
// ``POST /api/scan/trigger`` sets an event and returns; the scan runs on
// kato's scan-loop thread afterwards. The Scan-now button used to stay busy
// only for the lifetime of that POST, so it blinked once and went idle while
// kato was still scanning — the operator clicked and, as far as the UI showed,
// nothing happened.
//
// Two phases, and the first one is why this isn't a plain "poll until false":
//
//   1. PICK-UP — the scan loop may be mid-sleep when the event is set, so
//      ``scanning`` is still false for a moment after the trigger. Polling for
//      false immediately would return at once, every time. We wait up to
//      ``pickupMs`` for it to go true.
//   2. DRAIN — once it is true, wait for it to go false.
//
// If the scan finishes entirely inside the pick-up window we never observe
// ``true``; that is fine and indistinguishable from a fast scan, and the
// button simply settles. Bounded by ``timeoutMs`` so a wedged scan loop or a
// server that stops answering can never leave the button disabled forever —
// the operator must always be able to click again.

export const SCAN_POLL_INTERVAL_MS = 600;
export const SCAN_PICKUP_GRACE_MS = 4000;
export const SCAN_MAX_WAIT_MS = 10 * 60 * 1000;

const _sleep = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

export async function waitForScanToFinish({
  fetchStatus,
  sleep = _sleep,
  now = () => Date.now(),
  intervalMs = SCAN_POLL_INTERVAL_MS,
  pickupMs = SCAN_PICKUP_GRACE_MS,
  timeoutMs = SCAN_MAX_WAIT_MS,
  shouldContinue = () => true,
} = {}) {
  if (typeof fetchStatus !== 'function') { return 'unavailable'; }
  const started = now();
  let observedScanning = false;

  while (shouldContinue()) {
    const status = await fetchStatus();
    // No scan loop wired (webserver-only boot / setup mode): there is no state
    // to wait for, so stop instead of polling a question nothing will answer.
    if (status && status.available === false) { return 'unavailable'; }
    if (status && status.scanning) {
      observedScanning = true;
    } else if (observedScanning) {
      return 'finished';
    } else if (now() - started >= pickupMs) {
      // Never saw it start. Either it was already done, or the loop is not
      // picking the event up — either way, stop holding the button.
      return 'not-started';
    }
    if (now() - started >= timeoutMs) { return 'timeout'; }
    await sleep(intervalMs);
  }
  return 'cancelled';
}

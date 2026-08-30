// Tests for createPoller — the shared visibility-aware poll loop used by
// the module stores. Uses fake timers to drive the recursive setTimeout.

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { createPoller } from './createPoller.js';

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('createPoller', () => {
  test('ticks on the interval after start(), stops after stop()', () => {
    const tick = vi.fn();
    const poller = createPoller(tick, 1000);
    expect(poller.running).toBe(false);
    poller.start();
    expect(poller.running).toBe(true);

    vi.advanceTimersByTime(1000);
    expect(tick).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(2000);
    expect(tick).toHaveBeenCalledTimes(3);

    poller.stop();
    expect(poller.running).toBe(false);
    vi.advanceTimersByTime(5000);
    expect(tick).toHaveBeenCalledTimes(3); // no more ticks
  });

  test('start() is idempotent — a second call does not double the cadence', () => {
    const tick = vi.fn();
    const poller = createPoller(tick, 1000);
    poller.start();
    poller.start();
    vi.advanceTimersByTime(1000);
    expect(tick).toHaveBeenCalledTimes(1);
    poller.stop();
  });

  test('skips the tick while the document is hidden, resumes when visible', () => {
    const tick = vi.fn();
    const poller = createPoller(tick, 1000);
    poller.start();

    vi.stubGlobal('document', { hidden: true });
    vi.advanceTimersByTime(2000);
    expect(tick).not.toHaveBeenCalled(); // hidden → skipped, but still reschedules

    vi.stubGlobal('document', { hidden: false });
    vi.advanceTimersByTime(1000);
    expect(tick).toHaveBeenCalledTimes(1); // resumes without a restart
    poller.stop();
  });

  test('returning to the tab ticks immediately, without waiting out the interval', () => {
    // Going quiet while hidden is only half the deal. Without this, a poller
    // that skipped ticks for ten minutes also leaves the view stale for a
    // full interval at the moment someone looks at it — and for the task
    // cache that means the operator reading the wrong file contents.
    const tick = vi.fn();
    const poller = createPoller(tick, 5000);
    poller.start();

    vi.advanceTimersByTime(20000);
    expect(tick).toHaveBeenCalledTimes(4);

    document.dispatchEvent(new Event('visibilitychange'));
    expect(tick).toHaveBeenCalledTimes(5); // no timer advance needed
    poller.stop();
  });

  test('a visibilitychange while still hidden does not tick', () => {
    const tick = vi.fn();
    const poller = createPoller(tick, 1000);
    poller.start();
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });

    document.dispatchEvent(new Event('visibilitychange'));
    expect(tick).not.toHaveBeenCalled();

    delete document.hidden;
    poller.stop();
  });

  test('a stopped poller does not tick on visibilitychange', () => {
    // A leaked listener keeps firing requests for a poller that is gone.
    const tick = vi.fn();
    const poller = createPoller(tick, 1000);
    poller.start();
    poller.stop();

    document.dispatchEvent(new Event('visibilitychange'));
    expect(tick).not.toHaveBeenCalled();
  });

  test('survives a document with no addEventListener', () => {
    // The store tests swap document for a bare { hidden } object AFTER the
    // poller is built; resolving the listener per call keeps stop() from
    // throwing on it.
    const tick = vi.fn();
    const poller = createPoller(tick, 1000);
    poller.start();
    vi.stubGlobal('document', { hidden: false });
    expect(() => poller.stop()).not.toThrow();
  });
});

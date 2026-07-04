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
});

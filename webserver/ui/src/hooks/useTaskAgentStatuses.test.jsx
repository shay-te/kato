/**
 * Switching agent tabs must not make the status chips lie.
 *
 * A switch resets the live SSE stream, so for a moment the active chip has
 * nothing definite to say — and the other chip was up to a 5s poll behind.
 * The operator saw switching tabs "affect who is working", because for those
 * seconds neither chip reported the truth. The fix is an immediate re-poll
 * keyed on the active agent.
 */
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';

function deferred() {
  let resolve;
  const promise = new Promise((res) => { resolve = res; });
  return { promise, resolve };
}

vi.mock('../api.js', () => ({
  fetchTaskAgentStatus: vi.fn(),
}));

const { fetchTaskAgentStatus } = await import('../api.js');
const { useTaskAgentStatuses } = await import('./useTaskAgentStatuses.js');

const ROWS = [
  { id: 'claude', label: 'Claude', active: true, live: true, working: true },
];

beforeEach(() => {
  vi.clearAllMocks();
  fetchTaskAgentStatus.mockResolvedValue({ backends: ROWS });
});

describe('useTaskAgentStatuses', () => {
  test('reports the rows the server returns', async () => {
    const { result } = renderHook(() => useTaskAgentStatuses('T1'));
    await waitFor(() => expect(result.current).toEqual(ROWS));
  });

  test('no task means no rows and no request', () => {
    const { result } = renderHook(() => useTaskAgentStatuses(''));
    expect(result.current).toEqual([]);
    expect(fetchTaskAgentStatus).not.toHaveBeenCalled();
  });

  test('a switch re-polls IMMEDIATELY rather than waiting out the interval', async () => {
    const { rerender } = renderHook(
      ({ key }) => useTaskAgentStatuses('T1', { resyncKey: key }),
      { initialProps: { key: 'Claude' } },
    );
    await waitFor(() => expect(fetchTaskAgentStatus).toHaveBeenCalledTimes(1));

    rerender({ key: 'Codex' });
    await waitFor(() => expect(fetchTaskAgentStatus).toHaveBeenCalledTimes(2));
  });

  test('a re-render with the SAME agent does not re-poll', async () => {
    const { rerender } = renderHook(
      ({ key }) => useTaskAgentStatuses('T1', { resyncKey: key }),
      { initialProps: { key: 'Claude' } },
    );
    await waitFor(() => expect(fetchTaskAgentStatus).toHaveBeenCalledTimes(1));
    rerender({ key: 'Claude' });
    expect(fetchTaskAgentStatus).toHaveBeenCalledTimes(1);
  });

  test('a failed poll keeps the last known rows rather than blanking', async () => {
    const { result } = renderHook(() => useTaskAgentStatuses('T1'));
    await waitFor(() => expect(result.current).toEqual(ROWS));
    fetchTaskAgentStatus.mockRejectedValue(new Error('offline'));
    // One dropped request must not flash "unknown" at the operator.
    expect(result.current).toEqual(ROWS);
  });

  test('a malformed payload yields no rows, not a crash', async () => {
    fetchTaskAgentStatus.mockResolvedValue({ backends: 'nope' });
    const { result } = renderHook(() => useTaskAgentStatuses('T1'));
    await waitFor(() => expect(fetchTaskAgentStatus).toHaveBeenCalled());
    expect(result.current).toEqual([]);
  });
});

// Request ordering.
//
// The old loop rescheduled in .finally(), so exactly one request was ever in
// flight. Polling on a fixed cadence can have two, and a slow first response
// landing after a fast second one would apply the OLDER chips and leave them
// there until the next tick — showing an agent as working after it stopped, or
// the reverse. The task-generation guard cannot catch this: both responses
// belong to the same task.
describe('useTaskAgentStatuses — a late response never overwrites a newer one', () => {
  test('an overtaken response is dropped', async () => {
    // Fake timers so NO further tick can fire between the stale response
    // landing and the assertion. With a real interval a subsequent poll
    // repaints the correct value and masks the bug entirely — the first
    // version of this test passed against the unfixed hook for that reason.
    vi.useFakeTimers();
    try {
      const first = deferred();
      fetchTaskAgentStatus
        .mockReturnValueOnce(first.promise)                              // tick 1: hangs
        .mockResolvedValue({ backends: [{ id: 'claude', live: false }] }); // tick 2

      const { result } = renderHook(
        () => useTaskAgentStatuses('T1', { intervalMs: 1000 }),
      );

      // Tick 2 lands and paints "not live" — the NEWEST answer.
      await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
      expect(result.current[0]?.live).toBe(false);

      // The overtaken first response finally resolves. Only microtasks are
      // flushed, so no tick can repaint over it.
      await act(async () => {
        first.resolve({ backends: [{ id: 'claude', live: true }] });
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(result.current[0]?.live).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });
});

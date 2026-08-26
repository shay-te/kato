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
import { renderHook, waitFor } from '@testing-library/react';

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

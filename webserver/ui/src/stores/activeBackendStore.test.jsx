/**
 * The selected agent TAB, not the session record's idea of it.
 *
 * Every consumer read ``session.agent_backend`` — a field on the 5s-polled
 * /api/sessions record. Right after a switch that field is still the PREVIOUS
 * value, and two operator-visible bugs came out of the substitution:
 *
 *   * a message typed in the Codex tab was tagged ``claude``; the server's
 *     "the tab is authoritative" rule then re-pointed the record, so the
 *     chat visibly moved to Claude on the next refresh ("i put a task on
 *     codex chat, refresh the page, it moved to claude chat");
 *   * the banner's Upgrade button, labelled "update Codex", ran the CLAUDE
 *     upgrade command.
 */
import { describe, test, expect, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import {
  activeBackendStore, useActiveBackend,
} from './activeBackendStore.js';

beforeEach(() => { activeBackendStore.clearAll(); });

describe('activeBackendStore', () => {
  test('remembers the selected tab per task', () => {
    activeBackendStore.set('T1', 'codex');
    activeBackendStore.set('T2', 'claude');
    expect(activeBackendStore.get('T1')).toBe('codex');
    expect(activeBackendStore.get('T2')).toBe('claude');
  });

  test('normalises what it is given', () => {
    activeBackendStore.set('T1', '  CODEX ');
    expect(activeBackendStore.get('T1')).toBe('codex');
  });

  test('an unknown task has no answer', () => {
    expect(activeBackendStore.get('nope')).toBe('');
    expect(activeBackendStore.get('')).toBe('');
  });

  test('clearing a task drops its answer', () => {
    activeBackendStore.set('T1', 'codex');
    activeBackendStore.clear('T1');
    expect(activeBackendStore.get('T1')).toBe('');
  });

  test('subscribers are notified on change', () => {
    let calls = 0;
    const stop = activeBackendStore.subscribe(() => { calls += 1; });
    const afterSubscribe = calls;   // fires once immediately
    activeBackendStore.set('T1', 'codex');
    expect(calls).toBeGreaterThan(afterSubscribe);
    stop();
  });

  test('setting the same value does not notify', () => {
    activeBackendStore.set('T1', 'codex');
    let calls = 0;
    const stop = activeBackendStore.subscribe(() => { calls += 1; });
    const afterSubscribe = calls;
    activeBackendStore.set('T1', 'codex');
    expect(calls).toBe(afterSubscribe);
    stop();
  });
});

describe('useActiveBackend', () => {
  test('falls back to the record until the tab strip reports in', () => {
    // First paint: nothing has been selected yet, and the record IS correct.
    const { result } = renderHook(() => useActiveBackend('T1', 'claude'));
    expect(result.current).toBe('claude');
  });

  test('the selected tab WINS over a stale record', () => {
    // The exact bug: the operator is on Codex, the poll still says claude.
    const { result } = renderHook(() => useActiveBackend('T1', 'claude'));
    act(() => { activeBackendStore.set('T1', 'codex'); });
    expect(result.current).toBe('codex');
  });

  test('it tracks a later switch back', () => {
    const { result } = renderHook(() => useActiveBackend('T1', 'claude'));
    act(() => { activeBackendStore.set('T1', 'codex'); });
    act(() => { activeBackendStore.set('T1', 'claude'); });
    expect(result.current).toBe('claude');
  });

  test('another task’s selection does not leak in', () => {
    const { result } = renderHook(() => useActiveBackend('T1', 'claude'));
    act(() => { activeBackendStore.set('T2', 'codex'); });
    expect(result.current).toBe('claude');
  });

  test('no task and no record yields nothing', () => {
    const { result } = renderHook(() => useActiveBackend('', ''));
    expect(result.current).toBe('');
  });
});

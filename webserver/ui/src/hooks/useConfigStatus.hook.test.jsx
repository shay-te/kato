// Tests for useConfigStatus — the single source of truth behind the
// first-run setup gate. Polls /api/config-status and exposes a manual
// refresh the wizard fires right after each save.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchConfigStatus: vi.fn(),
}));

import { fetchConfigStatus } from '../api.js';
import { useConfigStatus } from './useConfigStatus.js';

beforeEach(() => {
  fetchConfigStatus.mockReset();
});

describe('useConfigStatus', () => {
  test('loads the status on mount', async () => {
    fetchConfigStatus.mockResolvedValue(
      { setup_mode: true, needs_config: true, missing: ['x'] },
    );
    const { result } = renderHook(() => useConfigStatus());
    await waitFor(() => {
      expect(result.current.status).not.toBeNull();
    });
    expect(result.current.status.setup_mode).toBe(true);
    expect(result.current.status.missing).toEqual(['x']);
  });

  test('manual refresh picks up the post-save server state immediately', async () => {
    fetchConfigStatus.mockResolvedValue(
      { setup_mode: true, needs_config: true, missing: ['x'] },
    );
    const { result } = renderHook(() => useConfigStatus());
    await waitFor(() => {
      expect(result.current.status).not.toBeNull();
    });
    // The wizard saved a setting; the server now reports configured.
    fetchConfigStatus.mockResolvedValue(
      { setup_mode: true, needs_config: false, missing: [] },
    );
    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current.status.needs_config).toBe(false);
  });

  test('a failed poll keeps the last known status instead of crashing', async () => {
    fetchConfigStatus.mockResolvedValue(
      { setup_mode: false, needs_config: false, missing: [] },
    );
    const { result } = renderHook(() => useConfigStatus());
    await waitFor(() => {
      expect(result.current.status).not.toBeNull();
    });
    fetchConfigStatus.mockRejectedValue(new Error('server restarting'));
    await act(async () => {
      await result.current.refresh();
    });
    // Still the last good value — the app keeps rendering.
    expect(result.current.status.setup_mode).toBe(false);
  });
});

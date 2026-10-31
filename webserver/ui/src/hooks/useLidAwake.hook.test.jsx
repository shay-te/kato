// Tests for useLidAwake: what the header's lid chip reads.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchLidAwake: vi.fn(),
}));

import { fetchLidAwake } from '../api.js';
import { useLidAwake } from './useLidAwake.js';

beforeEach(() => {
  fetchLidAwake.mockReset();
});

describe('useLidAwake', () => {
  test('starts empty and loads the status on mount', async () => {
    fetchLidAwake.mockResolvedValue({ label: 'Lid can close' });
    const { result } = renderHook(() => useLidAwake());
    expect(result.current).toBeNull();
    await waitFor(() => {
      expect(result.current).toEqual({ label: 'Lid can close' });
    });
  });

  test('an older backend with no such route leaves it empty', async () => {
    fetchLidAwake.mockRejectedValue(new Error('404'));
    const { result } = renderHook(() => useLidAwake());
    await waitFor(() => {
      expect(fetchLidAwake).toHaveBeenCalled();
    });
    expect(result.current).toBeNull();
  });
});

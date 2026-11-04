// Shutting kato down from the UI: asks first, names the agents that continue.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchShutdownPreview: vi.fn(),
  shutdownKato: vi.fn(),
}));
vi.mock('../stores/toastStore.js', () => ({
  toast: { show: vi.fn(), errorFromResult: vi.fn() },
}));

import { fetchShutdownPreview, shutdownKato } from '../api.js';
import { toast } from '../stores/toastStore.js';
import ShutdownButton, { shutdownConfirm, shutdownNote } from './ShutdownButton.jsx';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ShutdownButton', () => {
  test('asks first, naming the agents that continue; then shuts down and says so', async () => {
    fetchShutdownPreview.mockResolvedValue({ running: ['UNA-1', 'UNA-3'] });
    shutdownKato.mockResolvedValue({ ok: true, body: { running: ['UNA-1', 'UNA-3'], shutting_down: true } });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<ShutdownButton />);
    fireEvent.click(screen.getByRole('button', { name: 'Shut down kato' }));
    await waitFor(() => expect(shutdownKato).toHaveBeenCalled());
    expect(confirm.mock.calls[0][0]).toBe(
      'Shut down kato? 2 agents are working (UNA-1, UNA-3). They are stopped now and continue where '
      + 'they stopped when kato starts again.',
    );
    await waitFor(() => expect(toast.show).toHaveBeenCalledWith(expect.objectContaining({
      title: 'Kato is shutting down', message: 'Start kato again and UNA-1, UNA-3 continue where they stopped.',
      durationMs: 0,
    })));
    confirm.mockRestore();
  });

  test('cancelled, nothing stops', async () => {
    fetchShutdownPreview.mockResolvedValue({ running: [] });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<ShutdownButton />);
    fireEvent.click(screen.getByRole('button', { name: 'Shut down kato' }));
    await waitFor(() => expect(confirm).toHaveBeenCalled());
    expect(shutdownKato).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  test('a refusal is shown; an unreadable preview still asks', async () => {
    fetchShutdownPreview.mockRejectedValue(new Error('offline'));
    shutdownKato.mockResolvedValue({ ok: false, error: 'kato did not answer' });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<ShutdownButton />);
    fireEvent.click(screen.getByRole('button', { name: 'Shut down kato' }));
    await waitFor(() => expect(toast.errorFromResult).toHaveBeenCalled());
    expect(confirm.mock.calls[0][0]).toBe('Shut down kato? No agent is working right now.');
    confirm.mockRestore();
  });

  test('one agent, and none', () => {
    expect(shutdownConfirm(['UNA-1'])).toMatch(/1 agent is working \(UNA-1\)/);
    expect(shutdownNote(['UNA-1'])).toBe('Start kato again and UNA-1 continues where it stopped.');
    expect(shutdownNote([])).toBe('Start kato again to pick up where you left off.');
  });
});

/**
 * The Upgrade button must upgrade the CLI the banner is talking about.
 *
 * Reported with a screenshot: a banner reading "CODEX CLI update available —
 * you're on 0.145.0" ran ``/Users/…/claude update``. The server was correct
 * throughout — given ``codex`` it builds ``npm install -g @openai/codex``.
 * The client sent nothing, because ``start`` was a useCallback whose
 * dependency array omitted ``backend``: it captured the value from the render
 * that created it (empty, before the active tab resolved) and never updated,
 * while the banner around it re-rendered correctly. The server then fell back
 * to the CONFIGURED backend, which is Claude.
 */
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  upgradeAgentCli: vi.fn(async () => ({ ok: true, body: { state: 'done' } })),
  fetchAgentUpgradeStatus: vi.fn(async () => ({ state: 'idle' })),
  fetchAgentVersion: vi.fn(async () => ({})),
  fetchModels: vi.fn(async () => ({ models: [] })),
  fetchEffortLevels: vi.fn(async () => ({ levels: [] })),
  fetchOpenRouterModels: vi.fn(async () => []),
}));

const { upgradeAgentCli } = await import('../api.js');
const { useAgentUpgrade } = await import('./useAgentUpgrade.js');

beforeEach(() => { vi.clearAllMocks(); });

describe('useAgentUpgrade', () => {
  test('upgrades the backend it was given', async () => {
    const { result } = renderHook(() => useAgentUpgrade('codex'));
    await act(async () => { await result.current.start(); });
    expect(upgradeAgentCli).toHaveBeenCalledWith('codex');
  });

  test('a backend that arrives LATE is still used — the reported bug', async () => {
    // First render has no backend (the active tab has not resolved yet);
    // the banner then re-renders with 'codex'. The click happens after.
    const { result, rerender } = renderHook(
      ({ backend }) => useAgentUpgrade(backend),
      { initialProps: { backend: '' } },
    );
    rerender({ backend: 'codex' });

    await act(async () => { await result.current.start(); });

    expect(upgradeAgentCli).toHaveBeenCalledWith('codex');
    expect(upgradeAgentCli).not.toHaveBeenCalledWith('');
  });

  test('switching tabs before clicking upgrades the NEW tab', async () => {
    const { result, rerender } = renderHook(
      ({ backend }) => useAgentUpgrade(backend),
      { initialProps: { backend: 'claude' } },
    );
    rerender({ backend: 'codex' });
    await act(async () => { await result.current.start(); });
    expect(upgradeAgentCli).toHaveBeenLastCalledWith('codex');
  });

  test('no backend sends none, letting the server pick the configured one', async () => {
    const { result } = renderHook(() => useAgentUpgrade());
    await act(async () => { await result.current.start(); });
    expect(upgradeAgentCli).toHaveBeenCalledWith('');
  });

  test('a failed request does not leave the bar stuck on "starting"', async () => {
    upgradeAgentCli.mockRejectedValueOnce(new Error('offline'));
    const { result } = renderHook(() => useAgentUpgrade('codex'));
    await act(async () => { await result.current.start(); });
    await waitFor(() => expect(result.current.progress).toBeTruthy());
  });
});

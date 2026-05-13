// Tests for FilesTab. The file is 632 lines composing the file
// tree + commit dropdown + sync action. We focus on the pure
// helper that maps sync api results to toast shape — every other
// behavior is a render-only composition of well-tested deps
// (react-arborist for the tree).

import { describe, test, expect, vi } from 'vitest';
import { render } from '@testing-library/react';

vi.mock('./api.js', () => ({
  fetchTaskFiles: vi.fn().mockResolvedValue({ ok: true, body: { repos: [] } }),
  fetchFileContent: vi.fn(),
  syncRepositoriesNow: vi.fn(),
  fetchTaskCommits: vi.fn().mockResolvedValue({ ok: true, body: [] }),
}));
vi.mock('./stores/toastStore.js', () => ({
  toast: { show: vi.fn() },
}));

import FilesTab, { formatSyncResult } from './FilesTab.jsx';


describe('formatSyncResult — toast shape for /api/sync-repositories', () => {

  test('failed request → error toast with the api message', () => {
    expect(formatSyncResult({ ok: false, error: 'timeout' })).toEqual({
      kind: 'error',
      title: 'Sync repositories failed',
      message: 'timeout',
    });
  });

  test('failed request → error toast falls back to body.error', () => {
    expect(formatSyncResult({
      ok: false, body: { error: 'auth' },
    })).toEqual({
      kind: 'error',
      title: 'Sync repositories failed',
      message: 'auth',
    });
  });

  test('failed request with no error → "unknown error" placeholder', () => {
    expect(formatSyncResult({ ok: false })).toEqual({
      kind: 'error',
      title: 'Sync repositories failed',
      message: 'unknown error',
    });
  });

  test('null result is treated as failed', () => {
    // Defensive — caller might pass a falsy value.
    expect(formatSyncResult(null)).toEqual({
      kind: 'error',
      title: 'Sync repositories failed',
      message: 'unknown error',
    });
  });

  test('all-failed → red error toast', () => {
    const result = formatSyncResult({
      ok: true,
      body: {
        added_repositories: [],
        failed_repositories: [
          { repository_id: 'r1', error: 'permission denied' },
          { repository_id: 'r2', error: 'not found' },
        ],
      },
    });
    expect(result.kind).toBe('error');
    expect(result.title).toBe('Sync failed');
    expect(result.message).toContain('r1: permission denied');
    expect(result.message).toContain('r2: not found');
  });

  test('partial success → amber warning toast', () => {
    const result = formatSyncResult({
      ok: true,
      body: {
        added_repositories: ['r1', 'r2'],
        failed_repositories: [{ repository_id: 'r3', error: 'auth' }],
      },
    });
    expect(result.kind).toBe('warning');
    expect(result.title).toBe('Sync partially succeeded');
    expect(result.message).toContain('added 2 repo(s)');
    expect(result.message).toContain('r3: auth');
  });

  test('nothing-to-add → green success toast with "already in sync" message', () => {
    // Operator clicks Sync when everything's already cloned. We
    // want a green toast saying so — never silent.
    const result = formatSyncResult({
      ok: true,
      body: { added_repositories: [], failed_repositories: [] },
    });
    expect(result.kind).toBe('success');
    expect(result.title).toMatch(/already in sync/i);
  });

  test('repos added cleanly → green success with the added list', () => {
    const result = formatSyncResult({
      ok: true,
      body: {
        added_repositories: ['client', 'backend'],
        failed_repositories: [],
      },
    });
    expect(result.kind).toBe('success');
    expect(result.title).toContain('Added 2');
    expect(result.message).toContain('client');
    expect(result.message).toContain('backend');
  });

  test('empty body produces "already in sync"', () => {
    const result = formatSyncResult({ ok: true, body: {} });
    expect(result.kind).toBe('success');
    expect(result.title).toMatch(/already in sync/i);
  });
});


describe('FilesTab — render shell', () => {

  test('renders without crashing when activeTaskId is null', () => {
    const { container } = render(
      <FilesTab activeTaskId={null} onAddToChat={vi.fn()} />,
    );
    expect(container).toBeInTheDocument();
  });
});

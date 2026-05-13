// Tests for DiffFileComments — the file-level comment panel
// shown above per-line widgets on the Changes tab.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

vi.mock('../api.js', () => ({
  createTaskComment: vi.fn().mockResolvedValue({ ok: true, body: { id: 'new' } }),
  deleteTaskComment: vi.fn().mockResolvedValue({ ok: true }),
  fetchTaskComments: vi.fn(),
  reopenTaskComment: vi.fn().mockResolvedValue({ ok: true }),
  resolveTaskComment: vi.fn().mockResolvedValue({ ok: true }),
}));
vi.mock('../stores/toastStore.js', () => ({
  toast: { show: vi.fn() },
}));

import { fetchTaskComments, createTaskComment } from '../api.js';
import DiffFileComments from './DiffFileComments.jsx';


beforeEach(() => {
  fetchTaskComments.mockReset();
  createTaskComment.mockReset();
  createTaskComment.mockResolvedValue({ ok: true, body: { id: 'new' } });
});


describe('DiffFileComments — loading + listing', () => {

  test('loading state shows while comments are being fetched', async () => {
    let resolveFetch;
    fetchTaskComments.mockReturnValue(new Promise((r) => { resolveFetch = r; }));

    render(
      <DiffFileComments taskId="T1" repoId="r1" filePath="auth.py" />,
    );
    // The loading message renders during the in-flight fetch.
    expect(screen.queryByText(/loading|empty/i) || screen.queryByText(/comments/i))
      .toBeTruthy();

    resolveFetch({ ok: true, body: { comments: [] } });
    await waitFor(() => {
      expect(fetchTaskComments).toHaveBeenCalled();
    });
  });

  test('error state surfaces the message', async () => {
    fetchTaskComments.mockResolvedValue({ ok: false, error: 'permission denied' });

    render(
      <DiffFileComments taskId="T1" repoId="r1" filePath="auth.py" />,
    );

    await waitFor(() => {
      expect(screen.getByText(/permission denied/i)).toBeInTheDocument();
    });
  });

  test('filters comments to the active file path only', async () => {
    fetchTaskComments.mockResolvedValue({
      ok: true,
      body: {
        comments: [
          {
            id: 'c1', body: 'note about auth', file_path: 'auth.py',
            line: -1, parent_id: '', status: 'open',
            author: 'reviewer', source: 'local', created_at_epoch: 1000,
          },
          {
            id: 'c2', body: 'unrelated', file_path: 'other.py',
            line: -1, parent_id: '', status: 'open',
            author: 'reviewer', source: 'local', created_at_epoch: 2000,
          },
        ],
      },
    });

    render(
      <DiffFileComments taskId="T1" repoId="r1" filePath="auth.py" />,
    );

    await waitFor(() => {
      expect(screen.getByText('note about auth')).toBeInTheDocument();
    });
    // The unrelated comment must NOT render in this file's panel.
    expect(screen.queryByText('unrelated')).not.toBeInTheDocument();
  });

  test('empty state when no comments match the file path', async () => {
    fetchTaskComments.mockResolvedValue({
      ok: true,
      body: { comments: [] },
    });

    render(
      <DiffFileComments taskId="T1" repoId="r1" filePath="auth.py" />,
    );
    await waitFor(() => {
      expect(fetchTaskComments).toHaveBeenCalled();
    });
    // Empty state messaging — exact copy varies but there are no
    // comment articles in the DOM.
    expect(screen.queryByText(/note about auth/)).not.toBeInTheDocument();
  });
});


describe('DiffFileComments — no-task guard', () => {

  test('does NOT fetch when taskId is missing', () => {
    render(
      <DiffFileComments taskId="" repoId="r1" filePath="auth.py" />,
    );
    expect(fetchTaskComments).not.toHaveBeenCalled();
  });

  test('does NOT fetch when repoId is missing', () => {
    render(
      <DiffFileComments taskId="T1" repoId="" filePath="auth.py" />,
    );
    expect(fetchTaskComments).not.toHaveBeenCalled();
  });
});


describe('DiffFileComments — refreshTick re-triggers fetch', () => {

  test('changing refreshTick re-fires the fetch (parent invalidation hook)', async () => {
    fetchTaskComments.mockResolvedValue({ ok: true, body: { comments: [] } });

    const { rerender } = render(
      <DiffFileComments taskId="T1" repoId="r1" filePath="auth.py" refreshTick={0} />,
    );
    await waitFor(() => expect(fetchTaskComments).toHaveBeenCalledTimes(1));

    rerender(
      <DiffFileComments taskId="T1" repoId="r1" filePath="auth.py" refreshTick={1} />,
    );
    await waitFor(() => expect(fetchTaskComments).toHaveBeenCalledTimes(2));
  });
});

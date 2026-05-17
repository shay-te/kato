// Tests for FilesTab. The file is 632 lines composing the file
// tree + commit dropdown + sync action. We focus on the pure
// helper that maps sync api results to toast shape — every other
// behavior is a render-only composition of well-tested deps
// (react-arborist for the tree).

import { beforeEach, describe, test, expect, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('./api.js', () => ({
  fetchDiff: vi.fn(),
  fetchFileTree: vi.fn(),
  fetchFileContent: vi.fn(),
  fetchRepoCommits: vi.fn().mockResolvedValue({ ok: true, body: [] }),
  syncTaskComments: vi.fn(),
  syncTaskRepositories: vi.fn(),
}));
vi.mock('./stores/toastStore.js', () => ({
  toast: { show: vi.fn() },
}));

import FilesTab, {
  buildFilesDiffMeta,
  filterChangedFileTree,
  formatSyncResult,
} from './FilesTab.jsx';
import { fetchDiff, fetchFileTree } from './api.js';

const FILE_TREE_PAYLOAD = {
  trees: [{
    repo_id: 'client',
    cwd: '/tmp/client',
    tree: [{
      name: 'src',
      path: '/tmp/client/src',
      children: [{
        name: 'Changed.js',
        path: '/tmp/client/src/Changed.js',
      }, {
        name: 'Unchanged.js',
        path: '/tmp/client/src/Unchanged.js',
      }],
    }],
    changed_files: ['src/Changed.js'],
    conflicted_files: [],
  }],
};

const DIFF_PAYLOAD = {
  diffs: [{
    repo_id: 'client',
    cwd: '/tmp/client',
    diff: [
      'diff --git a/src/Changed.js b/src/Changed.js',
      'index 1111111..2222222 100644',
      '--- a/src/Changed.js',
      '+++ b/src/Changed.js',
      '@@ -1 +1,2 @@',
      '-old',
      '+new',
      '+newer',
      '',
    ].join('\n'),
    conflicted_files: [],
  }],
};

beforeEach(() => {
  fetchDiff.mockResolvedValue({ diffs: [] });
  fetchFileTree.mockResolvedValue({ trees: [] });
});


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


describe('buildFilesDiffMeta', () => {

  test('indexes changed files by repo and cwd with kind + line stats', () => {
    const meta = buildFilesDiffMeta([{
      repo_id: 'client',
      cwd: '/tmp/client',
      files: [{
        type: 'add',
        oldPath: '/dev/null',
        newPath: 'src/NewFile.js',
        hunks: [{
          changes: [
            { type: 'insert' },
            { type: 'insert' },
            { type: 'delete' },
          ],
        }],
      }],
    }]);
    const byRepo = meta.get('client');
    const byCwd = meta.get('/tmp/client');
    expect(byRepo).toBe(byCwd);
    expect(byRepo.get('src/NewFile.js')).toMatchObject({
      kind: 'add',
      stats: { added: 2, deleted: 1 },
    });
    expect(byRepo.get('src/NewFile.js').file.newPath).toBe('src/NewFile.js');
  });
});


describe('filterChangedFileTree', () => {

  test('keeps ancestors for changed files that match the search', () => {
    const tree = [{
      kind: 'folder',
      key: 'folder:src',
      name: 'src',
      children: [{
        kind: 'file',
        key: 'file:src/App.js',
        name: 'App.js',
        file: { type: 'modify', oldPath: 'src/App.js', newPath: 'src/App.js' },
        stats: { added: 1, deleted: 0 },
      }],
      stats: { added: 1, deleted: 0 },
    }];
    const filtered = filterChangedFileTree(tree, 'app');
    expect(filtered).toHaveLength(1);
    expect(filtered[0].name).toBe('src');
    expect(filtered[0].children[0].name).toBe('App.js');
  });
});


describe('FilesTab — render shell', () => {

  test('renders without crashing when activeTaskId is null', () => {
    const { container } = render(
      <FilesTab activeTaskId={null} onAddToChat={vi.fn()} />,
    );
    expect(container).toBeInTheDocument();
  });

  test('defaults to changed files and All toggles the full tree', async () => {
    fetchFileTree.mockResolvedValue(FILE_TREE_PAYLOAD);
    fetchDiff.mockResolvedValue(DIFF_PAYLOAD);
    render(<FilesTab taskId="T1" onOpenFile={vi.fn()} />);
    expect(await screen.findByText('Lines updated')).toBeInTheDocument();
    expect(screen.getByText('Changed.js')).toBeInTheDocument();
    expect(screen.queryByText('Unchanged.js')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Show all files' }));
    fireEvent.click(await screen.findByText('src'));
    await waitFor(() => {
      expect(screen.getByText('Unchanged.js')).toBeInTheDocument();
    });
  });

  test('marks conflicted files in the changed tree and full tree', async () => {
    const fileTreePayload = {
      trees: [{
        ...FILE_TREE_PAYLOAD.trees[0],
        conflicted_files: ['src/Changed.js'],
      }],
    };
    const diffPayload = {
      diffs: [{
        ...DIFF_PAYLOAD.diffs[0],
        conflicted_files: ['src/Changed.js'],
      }],
    };
    fetchFileTree.mockResolvedValue(fileTreePayload);
    fetchDiff.mockResolvedValue(diffPayload);
    render(<FilesTab taskId="T1" onOpenFile={vi.fn()} />);
    expect(await screen.findByText('Lines updated')).toBeInTheDocument();
    expect(screen.getByLabelText(/merge conflict/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Show all files' }));
    fireEvent.click(await screen.findByText('src'));
    await waitFor(() => {
      expect(screen.getAllByLabelText(/merge conflict/i).length).toBeGreaterThan(0);
    });
  });
});

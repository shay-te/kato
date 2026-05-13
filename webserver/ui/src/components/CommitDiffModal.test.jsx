// Component-level tests for CommitDiffModal — the per-commit diff
// modal triggered from the Files tab's "view commit" dropdown.
//
// Wiring under test:
//   - Renders the commit metadata: short sha, subject, author, repo id.
//   - Calls fetchRepoCommitDiff(taskId, repoId, sha) on mount.
//   - Shows a loading message while in flight.
//   - On ok:true with a diff string → renders the diff body.
//   - On ok:false → renders the error text.
//   - Empty diff string → renders the "no file changes" message.
//   - Close affordance dismisses the modal (onClose).

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchRepoCommitDiff: vi.fn(),
}));

import CommitDiffModal from './CommitDiffModal.jsx';
import { fetchRepoCommitDiff } from '../api.js';


const SAMPLE_DIFF = `diff --git a/foo.py b/foo.py
index 0000001..0000002 100644
--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,3 @@
 line one
-line two
+line two changed
 line three
`;


function _commit(extra = {}) {
  return {
    sha: extra.sha || 'abcdef0123456789',
    short_sha: extra.short_sha || 'abcdef01',
    subject: extra.subject ?? 'fix the parser',
    author: extra.author ?? 'Alice <alice@example.com>',
  };
}


function renderModal({
  taskId = 'TASK-1',
  repoId = 'repo-x',
  commit = _commit(),
  onClose = vi.fn(),
} = {}) {
  return {
    onClose,
    ...render(
      <CommitDiffModal
        taskId={taskId}
        repoId={repoId}
        commit={commit}
        onClose={onClose}
      />,
    ),
  };
}


beforeEach(() => {
  fetchRepoCommitDiff.mockReset();
});


describe('CommitDiffModal — metadata + header', () => {

  test('renders short sha + subject in the header', async () => {
    fetchRepoCommitDiff.mockReturnValue(new Promise(() => {}));  // hang in loading

    renderModal({
      commit: _commit({ short_sha: 'cafe0001', subject: 'tidy imports' }),
    });

    expect(screen.getByText('cafe0001')).toBeInTheDocument();
    expect(screen.getByText(/tidy imports/i)).toBeInTheDocument();
  });

  test('renders author + repo id in the help line', async () => {
    fetchRepoCommitDiff.mockReturnValue(new Promise(() => {}));

    renderModal({
      repoId: 'kato-ui',
      commit: _commit({ author: 'Bob' }),
    });

    expect(screen.getByText('Bob')).toBeInTheDocument();
    expect(screen.getByText('kato-ui')).toBeInTheDocument();
  });

  test('missing subject falls back to "(no subject)"', async () => {
    fetchRepoCommitDiff.mockReturnValue(new Promise(() => {}));

    renderModal({ commit: _commit({ subject: '' }) });

    expect(screen.getByText(/no subject/i)).toBeInTheDocument();
  });
});


describe('CommitDiffModal — fetch flow', () => {

  test('calls fetchRepoCommitDiff(taskId, repoId, sha) on mount', async () => {
    fetchRepoCommitDiff.mockResolvedValue({ ok: true, body: { diff: '' } });

    renderModal({
      taskId: 'T2',
      repoId: 'repo-y',
      commit: _commit({ sha: 'deadbeef' }),
    });

    await waitFor(() => {
      expect(fetchRepoCommitDiff).toHaveBeenCalledWith('T2', 'repo-y', 'deadbeef');
    });
  });

  test('loading message visible while api in flight', () => {
    fetchRepoCommitDiff.mockReturnValue(new Promise(() => {}));  // never resolves

    renderModal();

    expect(screen.getByText(/Loading commit diff/i)).toBeInTheDocument();
  });

  test('ok:true with non-empty diff: renders the diff body (file path)', async () => {
    fetchRepoCommitDiff.mockResolvedValue({ ok: true, body: { diff: SAMPLE_DIFF } });

    renderModal();

    await waitFor(() => {
      expect(screen.queryByText(/Loading commit diff/i)).not.toBeInTheDocument();
    });

    // react-diff-view renders the file path in the diff-file-path span.
    expect(screen.getByText('foo.py')).toBeInTheDocument();
  });

  test('ok:true with empty diff: renders "no file changes" message', async () => {
    fetchRepoCommitDiff.mockResolvedValue({ ok: true, body: { diff: '' } });

    renderModal();

    await waitFor(() => {
      expect(screen.getByText(/no file changes/i)).toBeInTheDocument();
    });
  });

  test('ok:false: renders the error message', async () => {
    fetchRepoCommitDiff.mockResolvedValue({ ok: false, error: 'sha not found' });

    renderModal();

    await waitFor(() => {
      expect(screen.getByText(/sha not found/i)).toBeInTheDocument();
    });
  });
});


describe('CommitDiffModal — close', () => {

  test('× close button dispatches onClose', () => {
    fetchRepoCommitDiff.mockReturnValue(new Promise(() => {}));
    const { onClose } = renderModal();

    fireEvent.click(screen.getByRole('button', { name: /^Close$/i }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('clicking the backdrop dispatches onClose', () => {
    fetchRepoCommitDiff.mockReturnValue(new Promise(() => {}));
    const { container, onClose } = renderModal();

    // The backdrop is the role="dialog" outer wrapper; clicking it
    // (not the inner panel) closes via the onClick handler.
    const backdrop = container.querySelector('.adopt-session-modal-backdrop');
    fireEvent.click(backdrop);

    expect(onClose).toHaveBeenCalled();
  });
});

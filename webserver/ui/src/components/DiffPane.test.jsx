// Tests for DiffPane — the centre-column diff viewer. It renders ONLY
// the selected file's diff; the left Changes list is the navigation
// surface that swaps which file shows. Heavy deps (the diff parser/path
// helpers, DiffFileWithComments, the chat-composer context, the API)
// are stubbed.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchDiff: vi.fn(),
  fetchTaskComments: vi.fn().mockResolvedValue({ ok: true, body: { comments: [] } }),
}));
vi.mock('../diffModel.js', () => ({
  parseRepoDiffs: vi.fn(),
  diffFileKey: (f) => `${f.type}:${f.oldPath || ''}->${f.newPath || ''}`,
  diffDisplayPath: (f) => {
    const real = (p) => (p && p !== '/dev/null' ? p : '');
    if (f.type === 'delete') { return real(f.oldPath) || real(f.newPath) || '(unknown)'; }
    return real(f.newPath) || real(f.oldPath) || '(unknown)';
  },
  isFileConflicted: (f, set) => {
    if (!set || set.size === 0) { return false; }
    return set.has(f.oldPath || '') || set.has(f.newPath || '');
  },
}));
vi.mock('./DiffFileWithComments.jsx', () => ({
  default: (props) => (
    <div
      data-testid="diff-file"
      data-path={props.file?.newPath || props.file?.oldPath}
      data-repo={props.repoId}
      data-initially-expanded={String(props.initiallyExpanded)}
      data-force-expand-token={String(props.forceExpandToken || 0)}
      data-conflicted={String(!!props.conflicted)}
      data-comments={String((props.comments || []).length)}
    >
      {(props.comments || []).length > 0 && (
        <article className="diff-file-comment-thread" data-testid="comment-thread" />
      )}
      <button
        type="button"
        onClick={() => props.onFocusInTree({
          repoId: props.repoId,
          relativePath: props.file?.newPath || props.file?.oldPath,
        })}
      >
        focus tree
      </button>
      <button type="button" onClick={() => props.onMutated()}>
        mutate comments
      </button>
    </div>
  ),
}));
vi.mock('../contexts/ChatComposerContext.jsx', () => ({
  useChatComposer: () => ({ appendToInput: vi.fn() }),
}));

import DiffPane, { diffAnchorKey } from './DiffPane.jsx';
import { fetchDiff, fetchTaskComments } from '../api.js';
import { parseRepoDiffs } from '../diffModel.js';


function _repoDiffs() {
  return [
    {
      repo_id: 'client',
      cwd: '/w/client',
      conflictedFiles: new Set(),
      files: [
        { type: 'modify', newPath: 'src/App.jsx', oldPath: 'src/App.jsx', hunks: [] },
        { type: 'add', newPath: 'src/new.js', oldPath: '/dev/null', hunks: [] },
      ],
    },
    {
      repo_id: 'backend',
      cwd: '/w/backend',
      conflictedFiles: new Set(['api/auth.py']),
      files: [{ type: 'modify', newPath: 'api/auth.py', oldPath: 'api/auth.py', hunks: [] }],
    },
  ];
}

const _open = (over = {}) => ({
  taskId: 'T1',
  absolutePath: '/w/client/src/App.jsx',
  relativePath: 'src/App.jsx',
  repoId: 'client',
  view: 'diff',
  ...over,
});


describe('diffAnchorKey', () => {
  test('joins repo + path; tolerates a missing repo', () => {
    expect(diffAnchorKey('client', 'src/App.jsx')).toBe('client::src/App.jsx');
    expect(diffAnchorKey('', 'a.js')).toBe('::a.js');
    expect(diffAnchorKey(undefined, 'a.js')).toBe('::a.js');
  });
});


describe('DiffPane — renders ONLY the selected file', () => {
  beforeEach(() => {
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    fetchDiff.mockReset();
    parseRepoDiffs.mockReset();
    fetchTaskComments.mockReset();
    fetchTaskComments.mockResolvedValue({ ok: true, body: { comments: [] } });
  });

  test('loading state while the diff fetch is in flight', () => {
    fetchDiff.mockReturnValue(new Promise(() => {}));
    render(<DiffPane openFile={_open()} />);
    expect(screen.getByText(/computing diff/i)).toBeInTheDocument();
  });

  test('renders the selected file and nothing else', async () => {
    fetchDiff.mockResolvedValue({ diffs: [] });
    parseRepoDiffs.mockReturnValue(_repoDiffs());
    render(<DiffPane openFile={_open()} />);
    const files = await screen.findAllByTestId('diff-file');
    // The changeset holds 3 files across 2 repos — only the selected
    // one renders in the centre pane.
    expect(files).toHaveLength(1);
    expect(files[0].getAttribute('data-path')).toBe('src/App.jsx');
    expect(files[0].getAttribute('data-repo')).toBe('client');
    expect(files[0].getAttribute('data-initially-expanded')).toBe('true');
    expect(fetchDiff).toHaveBeenCalledWith('T1');  // no repoId filter
  });

  test('selecting a file in another repo swaps the rendered diff', async () => {
    fetchDiff.mockResolvedValue({ diffs: [] });
    parseRepoDiffs.mockReturnValue(_repoDiffs());
    const { rerender } = render(<DiffPane openFile={_open()} />);
    await screen.findByTestId('diff-file');
    rerender(
      <DiffPane openFile={_open({ relativePath: 'api/auth.py', repoId: 'backend' })} />,
    );
    await waitFor(() => {
      expect(screen.getByTestId('diff-file').getAttribute('data-path'))
        .toBe('api/auth.py');
    });
  });

  test('a stale repoId still finds the file by path alone', async () => {
    fetchDiff.mockResolvedValue({ diffs: [] });
    parseRepoDiffs.mockReturnValue(_repoDiffs());
    render(
      <DiffPane openFile={_open({ relativePath: 'api/auth.py', repoId: 'gone' })} />,
    );
    const file = await screen.findByTestId('diff-file');
    expect(file.getAttribute('data-path')).toBe('api/auth.py');
    expect(file.getAttribute('data-repo')).toBe('backend');
  });

  test('refetches the diff when the workspace version changes', async () => {
    fetchDiff.mockResolvedValue({ diffs: [] });
    parseRepoDiffs.mockReturnValue(_repoDiffs());
    const { rerender } = render(
      <DiffPane openFile={_open()} workspaceVersion={1} />,
    );
    await screen.findByTestId('diff-file');
    expect(fetchDiff).toHaveBeenCalledTimes(1);

    rerender(<DiffPane openFile={_open()} workspaceVersion={2} />);
    await waitFor(() => {
      expect(fetchDiff).toHaveBeenCalledTimes(2);
    });
  });

  test('does not reparse unchanged diff payloads on workspace refresh', async () => {
    const payload = { diffs: [] };
    fetchDiff.mockResolvedValue(payload);
    parseRepoDiffs.mockReturnValue(_repoDiffs());
    const { rerender } = render(
      <DiffPane openFile={_open()} workspaceVersion={1} />,
    );
    await screen.findByTestId('diff-file');
    expect(parseRepoDiffs).toHaveBeenCalledTimes(1);

    rerender(<DiffPane openFile={_open()} workspaceVersion={2} />);
    await waitFor(() => {
      expect(fetchDiff).toHaveBeenCalledTimes(2);
    });
    expect(parseRepoDiffs).toHaveBeenCalledTimes(1);
  });

  test('fetches comments only for the selected file\'s repo', async () => {
    fetchDiff.mockResolvedValue({ diffs: [] });
    parseRepoDiffs.mockReturnValue(_repoDiffs());
    render(
      <DiffPane openFile={_open({ relativePath: 'api/auth.py', repoId: 'backend' })} />,
    );
    await screen.findByTestId('diff-file');
    await waitFor(() => {
      expect(fetchTaskComments).toHaveBeenCalledWith('T1', 'backend');
    });
    expect(fetchTaskComments).not.toHaveBeenCalledWith('T1', 'client');
  });

  test('the open request token reaches the rendered diff file', async () => {
    fetchDiff.mockResolvedValue({ diffs: [] });
    parseRepoDiffs.mockReturnValue(_repoDiffs());
    render(
      <DiffPane
        openFile={_open({
          relativePath: 'api/auth.py',
          repoId: 'backend',
          openRequestId: 7,
        })}
      />,
    );
    const file = await screen.findByTestId('diff-file');
    expect(file.getAttribute('data-force-expand-token')).toBe('7');
  });

  test('restores saved diff scroll position', async () => {
    fetchDiff.mockResolvedValue({ diffs: [] });
    parseRepoDiffs.mockReturnValue(_repoDiffs());
    const { container } = render(
      <DiffPane
        openFile={_open({
          diffScrollTop: 345,
          restoreViewState: true,
        })}
      />,
    );
    await screen.findByTestId('diff-file');
    const body = container.querySelector('.diff-pane-body');
    await waitFor(() => {
      expect(body.scrollTop).toBe(345);
    });
  });

  test('reports diff scroll position changes', async () => {
    fetchDiff.mockResolvedValue({ diffs: [] });
    parseRepoDiffs.mockReturnValue(_repoDiffs());
    const onViewStateChange = vi.fn();
    const { container } = render(
      <DiffPane openFile={_open()} onViewStateChange={onViewStateChange} />,
    );
    await screen.findByTestId('diff-file');
    const body = container.querySelector('.diff-pane-body');
    body.scrollTop = 222;
    fireEvent.scroll(body);
    expect(onViewStateChange).toHaveBeenCalledWith({ diffScrollTop: 222 });
  });

  test('focusComment scrolls to the file\'s first comment thread', async () => {
    fetchDiff.mockResolvedValue({ diffs: [] });
    parseRepoDiffs.mockReturnValue(_repoDiffs());
    fetchTaskComments.mockImplementation((_taskId, rid) => Promise.resolve(
      rid === 'backend'
        ? { ok: true, body: { comments: [{ id: 'c1', file_path: 'api/auth.py' }] } }
        : { ok: true, body: { comments: [] } },
    ));
    const { container } = render(
      <DiffPane
        openFile={_open({
          relativePath: 'api/auth.py', repoId: 'backend', focusComment: true,
        })}
      />,
    );
    await screen.findByTestId('diff-file');
    await waitFor(() => {
      const thread = container.querySelector(
        '[data-diff-key="backend::api/auth.py"] .diff-file-comment-thread',
      );
      expect(thread).toBeInTheDocument();
      expect(thread.scrollIntoView).toHaveBeenCalled();
    });
  });

  test('does NOT re-scroll to the thread when a later comments poll changes data (same open request)', async () => {
    // Regression: the focusComment effect depends on the comments state
    // (the thread only exists once comments load), and a poll that picked
    // up a new comment / status flip re-fired it and yanked the pane back
    // to the thread mid-read. It must centre the thread once per open
    // request, not on every comments refresh.
    const originalScrollIntoView = window.HTMLElement.prototype.scrollIntoView;
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    fetchDiff.mockResolvedValue({ diffs: [] });
    // Fresh array per call so a refetch changes state.repoDiffs identity
    // and the comments effect actually re-runs (a real poll).
    parseRepoDiffs.mockImplementation(() => _repoDiffs());
    fetchTaskComments.mockImplementation((_taskId, rid) => Promise.resolve(
      rid === 'backend'
        ? { ok: true, body: { comments: [{ id: 'c1', file_path: 'api/auth.py' }] } }
        : { ok: true, body: { comments: [] } },
    ));
    const open = _open({ relativePath: 'api/auth.py', repoId: 'backend', focusComment: true });
    const { container, rerender } = render(<DiffPane openFile={open} workspaceVersion={1} />);
    const fileNode = () => container.querySelector('[data-diff-key="backend::api/auth.py"]');
    // data-comments lives on the inner (mocked) diff-file element.
    const commentCount = () => fileNode()
      ?.querySelector('[data-testid="diff-file"]')
      ?.getAttribute('data-comments');
    await waitFor(() => {
      const thread = fileNode().querySelector('.diff-file-comment-thread');
      expect(thread).toBeInTheDocument();
      expect(thread.scrollIntoView).toHaveBeenCalled();
    });
    window.HTMLElement.prototype.scrollIntoView.mockClear();

    // Poll brings a SECOND comment (count 1→2, observable) → the comments
    // state re-builds with a new identity and the focusComment effect
    // re-fires; the requestId guard must keep the pane where the operator
    // left it.
    fetchTaskComments.mockImplementation((_taskId, rid) => Promise.resolve(
      rid === 'backend'
        ? { ok: true, body: { comments: [
            { id: 'c1', file_path: 'api/auth.py' },
            { id: 'c2', file_path: 'api/auth.py' },
          ] } }
        : { ok: true, body: { comments: [] } },
    ));
    rerender(<DiffPane openFile={open} workspaceVersion={2} />);
    await waitFor(() => expect(commentCount()).toBe('2'));
    await new Promise((resolve) => setTimeout(resolve, 25));
    expect(window.HTMLElement.prototype.scrollIntoView).not.toHaveBeenCalled();
    window.HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
  });

  test('conflicted file gets the conflicted flag', async () => {
    fetchDiff.mockResolvedValue({ diffs: [] });
    parseRepoDiffs.mockReturnValue(_repoDiffs());
    render(
      <DiffPane openFile={_open({ relativePath: 'api/auth.py', repoId: 'backend' })} />,
    );
    const file = await screen.findByTestId('diff-file');
    expect(file.getAttribute('data-conflicted')).toBe('true');
  });

  test('passes file-tree focus requests from the file header to the parent', async () => {
    fetchDiff.mockResolvedValue({ diffs: [] });
    parseRepoDiffs.mockReturnValue(_repoDiffs());
    const onFocusFileInTree = vi.fn();
    render(
      <DiffPane
        openFile={_open({ relativePath: 'api/auth.py', repoId: 'backend' })}
        onFocusFileInTree={onFocusFileInTree}
      />,
    );
    const button = await screen.findByRole('button', { name: /focus tree/i });
    fireEvent.click(button);
    expect(onFocusFileInTree).toHaveBeenCalledWith({
      repoId: 'backend',
      relativePath: 'api/auth.py',
    });
  });

  test('comment mutations ask the parent to refresh tree comment badges', async () => {
    fetchDiff.mockResolvedValue({ diffs: [] });
    parseRepoDiffs.mockReturnValue(_repoDiffs());
    const onCommentsChanged = vi.fn();
    render(
      <DiffPane openFile={_open()} onCommentsChanged={onCommentsChanged} />,
    );
    const button = await screen.findByRole('button', { name: /mutate comments/i });
    fireEvent.click(button);
    expect(onCommentsChanged).toHaveBeenCalledTimes(1);
  });

  test('empty changeset → "No changes on this task branch."', async () => {
    fetchDiff.mockResolvedValue({ diffs: [] });
    parseRepoDiffs.mockReturnValue([]);
    render(<DiffPane openFile={_open()} />);
    await waitFor(() => {
      expect(screen.getByText(/no changes on this task branch/i))
        .toBeInTheDocument();
    });
  });

  test('selected file missing from a non-empty changeset → per-file message', async () => {
    // E.g. Claude reverted the file between the click and the refresh.
    fetchDiff.mockResolvedValue({ diffs: [] });
    parseRepoDiffs.mockReturnValue(_repoDiffs());
    render(<DiffPane openFile={_open({ relativePath: 'gone.js', repoId: 'client' })} />);
    await waitFor(() => {
      expect(screen.getByText(/no changes in gone\.js/i)).toBeInTheDocument();
    });
    expect(screen.queryByTestId('diff-file')).not.toBeInTheDocument();
  });

  test('fetch failure surfaces the error', async () => {
    fetchDiff.mockRejectedValue(new Error('boom'));
    render(<DiffPane openFile={_open()} />);
    await waitFor(() => {
      expect(screen.getByText(/boom/)).toBeInTheDocument();
    });
  });

  test('no bound task → error, no fetch', () => {
    render(<DiffPane openFile={_open({ taskId: '' })} />);
    expect(screen.getByText(/no task bound/i)).toBeInTheDocument();
    expect(fetchDiff).not.toHaveBeenCalled();
  });
});

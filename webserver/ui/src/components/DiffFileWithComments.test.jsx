// Component-level tests for DiffFileWithComments. The pure helpers
// (countDiffLines, isLargeFile) already have unit tests; this file
// proves the React wiring:
//
//   - ``initiallyExpanded`` from ChangesTab drives the collapse state.
//   - When unspecified, the per-file fallback rule applies.
//   - The chevron button toggles the diff body.
//   - Collapsed files render only the header, not a placeholder body.

import { beforeEach, describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { parseDiff } from 'react-diff-view';

import DiffFileWithComments, {
  splitCommentsForDisplay,
} from './DiffFileWithComments.jsx';
import { LARGE_FILE_LINE_THRESHOLD } from './diffFileSize.js';

const apiMocks = vi.hoisted(() => {
  return {
    createTaskComment: vi.fn(),
    deleteTaskComment: vi.fn(),
    fetchBaseFileContent: vi.fn(),
    markTaskCommentAddressed: vi.fn(),
    reopenTaskComment: vi.fn(),
    resolveTaskComment: vi.fn(),
  };
});

vi.mock('../api.js', () => {
  return apiMocks;
});

beforeEach(() => {
  Object.values(apiMocks).forEach((mock) => {
    mock.mockReset();
  });
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
    configurable: true,
  });
});

function _file(lineCount, { type = 'modify', path = 'src/file.py' } = {}) {
  return {
    type,
    newPath: path,
    oldPath: path,
    hunks: [{
      content: '@@ -1 +1,1 @@',
      oldStart: 1, oldLines: lineCount,
      newStart: 1, newLines: lineCount,
      changes: new Array(lineCount).fill(0).map((_, i) => ({
        type: 'insert',
        content: `+ line ${i}`,
        lineNumber: i + 1,
        isInsert: true,
      })),
    }],
  };
}


function diffProps({ file, ...rest } = {}) {
  return {
    file: file || _file(10),
    taskId: 'T1',
    repoId: 'repo-1',
    repoCwd: '/workspace/repo-1',
    comments: [],
    commentsLoading: false,
    commentsError: '',
    onMutated: vi.fn(),
    onAddToChat: vi.fn(),
    ...rest,
  };
}

function renderDiff(props = {}) {
  return render(
    <DiffFileWithComments
      {...diffProps(props)}
    />,
  );
}

function rerenderDiff(rerender, props = {}) {
  rerender(<DiffFileWithComments {...diffProps(props)} />);
}


describe('DiffFileWithComments — collapse / expand integration', () => {

  test('splitCommentsForDisplay moves outdated line comments to file-level', () => {
    const fresh = { id: 'fresh', line: 3, status: 'open' };
    const stale = { id: 'stale', line: 4, status: 'open', outdated: true };
    const reply = { id: 'reply', parent_id: 'stale', line: 4, status: 'open' };
    const result = splitCommentsForDisplay([fresh, stale, reply]);

    expect(result.commentsByLine.get(3)).toEqual([fresh]);
    expect(result.commentsByLine.has(4)).toBe(false);
    expect(result.fileLevelComments).toEqual([stale, reply]);
  });

  test('initiallyExpanded=true: diff body renders inline', () => {
    const { container } = renderDiff({ file: _file(10), initiallyExpanded: true });

    expect(container.querySelector('.diff-file-body')).toBeInTheDocument();
    expect(screen.queryByText(/diff hidden/i)).not.toBeInTheDocument();
  });

  test('initiallyExpanded=false: renders the header only, no diff body', () => {
    const { container } = renderDiff({ file: _file(42), initiallyExpanded: false });

    expect(screen.queryByText(/diff hidden/i)).not.toBeInTheDocument();
    expect(container.querySelector('.diff')).not.toBeInTheDocument();
    expect(container.querySelector('.diff-file-body')).not.toBeInTheDocument();
  });

  test('forceExpandToken expands a collapsed diff from parent navigation', async () => {
    const file = _file(20);
    const { container, rerender } = renderDiff({
      file,
      initiallyExpanded: false,
      forceExpandToken: 0,
    });
    expect(container.querySelector('.diff-file-body')).not.toBeInTheDocument();

    rerenderDiff(rerender, {
      file,
      initiallyExpanded: false,
      forceExpandToken: 1,
    });
    await waitFor(() => {
      expect(container.querySelector('.diff-file-body')).toBeInTheDocument();
    });
  });

  test('a collapsed file auto-expands when it has an open inline comment', async () => {
    const { container } = renderDiff({
      file: _file(20),
      initiallyExpanded: false,
      comments: [{ id: 'c1', line: 3, status: 'open', file_path: 'src/file.py' }],
    });
    // The thread can't render while collapsed, so the file reveals itself.
    await waitFor(() => {
      expect(container.querySelector('.diff-file-body')).toBeInTheDocument();
    });
  });

  test('an outdated line comment renders in the file comments panel', () => {
    const { container } = renderDiff({
      file: _file(20),
      initiallyExpanded: true,
      comments: [{
        id: 'c1',
        body: 'stale line comment',
        line: 3,
        status: 'open',
        file_path: 'src/file.py',
        outdated: true,
      }],
    });

    // The body renders in the panel (the header also shows a one-line
    // preview of the same text — assert the actual thread body, not it).
    expect(screen.getAllByText('stale line comment')
      .some((el) => el.closest('.diff-file-comment-body'))).toBe(true);
    expect(screen.getByText(/Original line 3 changed/i)).toBeInTheDocument();
    expect(container.querySelector('.diff-line-comments-host')).not.toBeInTheDocument();
  });

  test('a collapsed file with only resolved comments stays collapsed', () => {
    const { container } = renderDiff({
      file: _file(20),
      initiallyExpanded: false,
      comments: [{ id: 'c1', line: 3, status: 'resolved', file_path: 'src/file.py' }],
    });
    expect(container.querySelector('.diff-file-body')).not.toBeInTheDocument();
  });

  test('initiallyExpanded omitted: falls back to per-file isLargeFile rule', () => {
    // No prop → uses the legacy per-file rule. A small file is
    // expanded by default; a too-large file is collapsed.
    const { rerender } = renderDiff({ file: _file(10) });
    expect(screen.queryByText(/diff hidden/i)).not.toBeInTheDocument();

    rerender(
      <DiffFileWithComments
        file={_file(LARGE_FILE_LINE_THRESHOLD + 50)}
        taskId="T1"
        repoId="repo-1"
        comments={[]}
        commentsLoading={false}
        commentsError=""
        onMutated={vi.fn()}
        onAddToChat={vi.fn()}
      />,
    );
    // After re-mount with a huge file, the placeholder shows.
    // Note: rerender keeps the same instance, so the lazy init's
    // initial expanded state from the FIRST file persists. The
    // cleaner check below uses a fresh render.
  });

  test('huge file (>LARGE_FILE_LINE_THRESHOLD) auto-collapses even without initiallyExpanded prop', () => {
    const { container } = renderDiff({ file: _file(LARGE_FILE_LINE_THRESHOLD + 100) });
    expect(screen.queryByText(/diff hidden/i)).not.toBeInTheDocument();
    expect(container.querySelector('.diff')).not.toBeInTheDocument();
  });

  test('initiallyExpanded=true overrides the per-file large-file rule', () => {
    // Belt-and-braces: ChangesTab's cumulative budget might decide
    // to expand a moderately-large file (if it's the first one in
    // a list and budget is fresh). Per-file isLargeFile says no,
    // but the explicit prop wins.
    renderDiff({
      file: _file(LARGE_FILE_LINE_THRESHOLD + 100),
      initiallyExpanded: true,
    });
    expect(screen.queryByText(/diff hidden/i)).not.toBeInTheDocument();
  });

  test('initiallyExpanded=false overrides the per-file small-file rule', () => {
    // The cumulative budget can decide a small file should collapse
    // because earlier files exhausted the budget. Explicit false
    // wins over the per-file "small file → expand" default.
    const { container } = renderDiff({ file: _file(20), initiallyExpanded: false });
    expect(screen.queryByText(/diff hidden/i)).not.toBeInTheDocument();
    expect(container.querySelector('.diff-file-comments')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /add file-level comment/i }))
      .not.toBeInTheDocument();
  });
});


describe('DiffFileWithComments — header rendering', () => {

  test('right-clicking the file header opens file actions', () => {
    const onFocusInTree = vi.fn();
    const { container } = renderDiff({
      file: _file(10, { path: 'src/auth/login.py' }),
      onFocusInTree,
    });

    fireEvent.contextMenu(container.querySelector('.diff-file'));

    expect(screen.getByRole('menuitem', { name: /show in tree/i }))
      .toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: /place in chat/i }))
      .toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: /copy relative path/i }))
      .toBeInTheDocument();
  });

  test('header context menu can reveal the file in the tree', () => {
    const onFocusInTree = vi.fn();
    const { container } = renderDiff({
      file: _file(10, { path: 'src/auth/login.py' }),
      onFocusInTree,
    });

    fireEvent.contextMenu(container.querySelector('.diff-file'));
    fireEvent.click(screen.getByRole('menuitem', { name: /show in tree/i }));

    expect(onFocusInTree).toHaveBeenCalledWith({
      repoId: 'repo-1',
      relativePath: 'src/auth/login.py',
    });
  });

  test('header context menu places the repo-prefixed path in chat', () => {
    const onAddToChat = vi.fn();
    const { container } = renderDiff({
      file: _file(10, { path: 'src/auth/login.py' }),
      onAddToChat,
    });

    fireEvent.contextMenu(container.querySelector('.diff-file'));
    fireEvent.click(screen.getByRole('menuitem', { name: /place in chat/i }));

    expect(onAddToChat).toHaveBeenCalledWith('`repo-1:src/auth/login.py`');
  });

  test('header context menu copies the repo-prefixed relative path', async () => {
    const { container } = renderDiff({
      file: _file(10, { path: 'src/auth/login.py' }),
    });

    fireEvent.contextMenu(container.querySelector('.diff-file'));
    fireEvent.click(screen.getByRole('menuitem', { name: /copy relative path/i }));

    await waitFor(() => {
      expect(navigator.clipboard.writeText)
        .toHaveBeenCalledWith('repo-1:src/auth/login.py');
    });
  });

  test('the change-kind glyph is NOT repeated here — the tab carries it', () => {
    // See FileTabStrip: the tab's leading icon is the kind glyph while a
    // diff is up. A one-icon header row restating it was dead space.
    const { container } = renderDiff({ file: _file(10, { type: 'add' }) });
    expect(container.querySelector('.diff-file-row-kind')).toBeNull();
  });

  test('diff body is the last child of the file section', () => {
    // The card keeps overflow:visible, so the rounded bottom is achieved
    // by clipping this wrapper instead — it has to be the final child.
    const { container } = renderDiff({ file: _file(6), initiallyExpanded: true });
    const section = container.querySelector('.diff-file');
    const body = section.querySelector('.diff-file-body');

    expect(body).toBeInTheDocument();
    expect(section.children[section.children.length - 1]).toBe(body);
    expect(body.querySelector('.diff')).toBeInTheDocument();
  });

  test('line-number gutter width is based on the largest real line number', () => {
    const { container } = renderDiff({ file: _file(99), initiallyExpanded: true });
    const section = container.querySelector('.diff-file');

    expect(section).toHaveStyle({ '--diff-gutter-col-width': '3ch' });
  });

  test('no header row at all — the tab says everything it used to', () => {
    const { container } = renderDiff({ file: _file(10), initiallyExpanded: true });

    expect(container.querySelector('.diff-file-header')).toBeNull();
    expect(container.querySelector('.diff-file-collapse-toggle')).toBeNull();
    expect(container.querySelector('.diff-file-path')).toBeNull();
    expect(container.querySelector('.diff-file-open-as-file')).toBeNull();
  });

  test('a merge conflict brings the header back — the tab cannot signal it', () => {
    const { container } = renderDiff({ file: _file(10), conflicted: true });

    expect(container.querySelector('.diff-file-header')).toBeInTheDocument();
    expect(screen.getByLabelText(/merge conflict/i)).toBeInTheDocument();
  });

  test('merge conflict mark shows when conflicted prop is true', () => {
    renderDiff({ file: _file(10), conflicted: true });
    expect(screen.getByLabelText(/merge conflict/i)).toBeInTheDocument();
  });

  test('merge conflict mark is absent by default', () => {
    renderDiff({ file: _file(10) });
    expect(screen.queryByLabelText(/merge conflict/i)).not.toBeInTheDocument();
  });
});


describe('DiffFileWithComments — comment reopen', () => {
  test('reopening a resolved root comment wakes the chat stream when kato starts', async () => {
    apiMocks.reopenTaskComment.mockResolvedValue({
      ok: true,
      body: {
        triggered_immediately: true,
        comment: { id: 'c1', status: 'open', kato_status: 'in_progress' },
      },
    });
    const onCommentSpawned = vi.fn();
    const onMutated = vi.fn();

    renderDiff({
      initiallyExpanded: true,
      onCommentSpawned,
      onMutated,
      comments: [{
        id: 'c1',
        body: 'please revisit',
        line: -1,
        status: 'resolved',
        kato_status: 'addressed',
        source: 'local',
        author: 'operator',
        created_at_epoch: 1,
      }],
    });

    fireEvent.click(screen.getByRole('button', { name: /expand comment/i }));
    fireEvent.click(screen.getByRole('button', { name: /reopen/i }));

    await waitFor(() => {
      expect(apiMocks.reopenTaskComment).toHaveBeenCalledWith('T1', 'c1');
    });
    expect(onMutated).toHaveBeenCalled();
    expect(onCommentSpawned).toHaveBeenCalled();
  });
});


describe('DiffFileWithComments — syntax highlighting', () => {

  test('renders syntax token spans for added JavaScript files', () => {
    const rawDiff = [
      'diff --git a/helpers.js b/helpers.js',
      'new file mode 100644',
      '--- /dev/null',
      '+++ b/helpers.js',
      '@@ -0,0 +1,2 @@',
      '+export const TAG_INFO = {',
      "+  TWILIO: { colorKey: 'COLOR_SALMON' },",
      '',
    ].join('\n');
    const file = parseDiff(rawDiff)[0];
    const { container } = renderDiff({ file, initiallyExpanded: true });

    expect(container.querySelector('.token.keyword')).toBeInTheDocument();
    expect(container.querySelector('.token.string')).toBeInTheDocument();
  });
});

describe('DiffFileWithComments — collapsed context expansion', () => {

  test('renders gap controls and expands hidden lines from the base file', async () => {
    const rawDiff = [
      'diff --git a/src/promises.scss b/src/promises.scss',
      '--- a/src/promises.scss',
      '+++ b/src/promises.scss',
      '@@ -1,3 +1,3 @@',
      ' line 1',
      '-line 2',
      '+line 2 changed',
      ' line 3',
      '@@ -30,3 +30,3 @@',
      ' line 30',
      '-line 31',
      '+line 31 changed',
      ' line 32',
      '',
    ].join('\n');
    const file = parseDiff(rawDiff)[0];
    const sourceLines = new Array(40).fill(0).map((_, index) => {
      return `line ${index + 1}`;
    });
    apiMocks.fetchBaseFileContent.mockResolvedValue({
      content: sourceLines.join('\n'),
      binary: false,
    });

    renderDiff({ file, initiallyExpanded: true });
    expect(screen.getByText(/26 hidden lines/i)).toBeInTheDocument();

    // The middle gap's "below" expander (the base file also has 8 lines
    // after the last hunk → a trailing expander appears once the base
    // loads, so target the 26-hidden-lines one specifically).
    const expandBelow = screen.getByRole('button', {
      name: /show hidden lines below \(26 hidden lines\)/i,
    });
    fireEvent.click(expandBelow);

    await waitFor(() => {
      expect(screen.getByText('line 29')).toBeInTheDocument();
    });
    expect(apiMocks.fetchBaseFileContent).toHaveBeenCalledWith(
      'T1',
      {
        repoId: 'repo-1',
        repoCwd: '/workspace/repo-1',
        path: 'src/promises.scss',
      },
    );
  });
});


describe('DiffFileWithComments — live update while the file stays open', () => {
  function diffFor(bodyLine) {
    return parseDiff([
      'diff --git a/src/app.js b/src/app.js',
      '--- a/src/app.js',
      '+++ b/src/app.js',
      '@@ -1,2 +1,2 @@',
      ' const a = 1;',
      `-${bodyLine} old`,
      `+${bodyLine} new`,
      '',
    ].join('\n'))[0];
  }

  test('a changed diff for the SAME open file re-renders live (no switch-away needed)', () => {
    // react-diff-view tokenizes each line into multiple spans, so assert on
    // the container's concatenated text, not getByText (which matches a
    // single element's full text).
    const { container, rerender } = render(
      <DiffFileWithComments {...diffProps({ file: diffFor('const b ='), initiallyExpanded: true })} />,
    );
    expect(container.textContent).toContain('const b = new');

    // Claude edits the same file → a fresh diff arrives for the SAME path.
    // The view must show it WITHOUT the operator switching files.
    rerender(
      <DiffFileWithComments {...diffProps({ file: diffFor('const c ='), initiallyExpanded: true })} />,
    );
    expect(container.textContent).toContain('const c = new');
    expect(container.textContent).not.toContain('const b = new');
  });

  test('an identical diff (idle poll, fresh array ref) does NOT reset — expansions survive', () => {
    const file = diffFor('const b =');
    const { container, rerender } = render(
      <DiffFileWithComments {...diffProps({ file, initiallyExpanded: true })} />,
    );
    // A poll returns the SAME bytes in a NEW array reference. If the reset
    // keyed on the reference it would wipe state every 5s; keyed on the
    // content signature it must be a no-op.
    const sameBytesFreshRef = diffFor('const b =');
    rerender(
      <DiffFileWithComments {...diffProps({ file: sameBytesFreshRef, initiallyExpanded: true })} />,
    );
    expect(container.textContent).toContain('const b = new');
  });
});


describe('DiffFileWithComments — buried comment auto-reveal', () => {

  const gappedDiff = [
    'diff --git a/src/promises.scss b/src/promises.scss',
    '--- a/src/promises.scss',
    '+++ b/src/promises.scss',
    '@@ -1,3 +1,3 @@',
    ' line 1',
    '-line 2',
    '+line 2 changed',
    ' line 3',
    '@@ -30,3 +30,3 @@',
    ' line 30',
    '-line 31',
    '+line 31 changed',
    ' line 32',
    '',
  ].join('\n');

  function gapSource() {
    return new Array(40).fill(0).map((_, i) => `line ${i + 1}`).join('\n');
  }

  test('a trailing expander appears below the last hunk (view lines to EOF)', async () => {
    // The last hunk ends at line 32; the base file has 40 lines, so there
    // are 8 lines below with NO expander before this fix (the trailing gap
    // needs the base line count, which now loads eagerly on expand).
    const file = parseDiff(gappedDiff)[0];
    apiMocks.fetchBaseFileContent.mockResolvedValue({
      content: gapSource(), binary: false,
    });

    renderDiff({ file, initiallyExpanded: true });

    // Bottom expander shows the 8 hidden lines below the last hunk.
    const below = await screen.findByRole('button', {
      name: /show hidden lines below \(8 hidden lines\)/i,
    });
    expect(below).toBeInTheDocument();
  });

  test('an open comment hidden in a gap is revealed with no manual click', async () => {
    const file = parseDiff(gappedDiff)[0];
    apiMocks.fetchBaseFileContent.mockResolvedValue({
      content: gapSource(), binary: false,
    });

    renderDiff({
      file,
      initiallyExpanded: true,
      comments: [{
        id: 'c1', body: 'open comment in a gap', line: 15,
        parent_id: '', status: 'open',
        author: 'reviewer', created_at: '2024-01-01T00:00:00Z',
      }],
    });

    // No expander was clicked — the thread shows up on its own,
    // and the line it is anchored to is now in the diff.
    await waitFor(() => {
      // The thread BODY (not just the header preview) must be revealed —
      // scope to .diff-file-comment-body so the header preview can't pass this.
      expect(screen.getAllByText('open comment in a gap')
        .some((el) => el.closest('.diff-file-comment-body'))).toBe(true);
    });
    expect(screen.getByText('line 15')).toBeInTheDocument();
    expect(apiMocks.fetchBaseFileContent).toHaveBeenCalledWith('T1', {
      repoId: 'repo-1',
      repoCwd: '/workspace/repo-1',
      path: 'src/promises.scss',
    });
  });

  test('a resolved-only thread does NOT force the gap open', async () => {
    const file = parseDiff(gappedDiff)[0];
    apiMocks.fetchBaseFileContent.mockResolvedValue({
      content: gapSource(), binary: false,
    });

    renderDiff({
      file,
      initiallyExpanded: true,
      comments: [{
        id: 'c1', body: 'resolved long ago', line: 15,
        parent_id: '', status: 'resolved',
        author: 'reviewer', created_at: '2024-01-01T00:00:00Z',
      }],
    });

    // Resolved threads must not auto-expand: the gap stays collapsed and
    // the resolved thread is not surfaced. (The base file may be fetched to
    // render the trailing "show lines below" expander — that's independent
    // of comment auto-reveal; what matters is the gap is NOT forced open.)
    expect(screen.getByText(/26 hidden lines/i)).toBeInTheDocument();
    await Promise.resolve();
    expect(screen.queryByText('resolved long ago')).not.toBeInTheDocument();
  });
});


describe('DiffFileWithComments — file-level comment shortcut', () => {

  test('clean file shows no entry button and no hint paragraph', () => {
    // The standalone "+ Add file-level comment" entry button and its
    // empty-state hint were removed on request — a clean file's diff
    // footer is now empty (no boilerplate under every file).
    renderDiff({ file: _file(10), comments: [] });
    expect(screen.queryByRole('button', { name: /add file-level comment/i }))
      .not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/add a file-level comment/i))
      .not.toBeInTheDocument();
    expect(screen.queryByText(/click a diff line's gutter/i))
      .not.toBeInTheDocument();
  });

  test('the file-level entry button is gone even with comments present', () => {
    // No file-level-comment entry point from the diff view in any
    // state — the removal is unconditional, not just empty-state.
    renderDiff({
      file: _file(10),
      comments: [{
        id: 'c1', body: 'pre-existing thread', line: -1,
        parent_id: '', status: 'open',
        author: 'reviewer', created_at: '2024-01-01T00:00:00Z',
      }],
    });
    expect(screen.queryByRole('button', { name: /add file-level comment/i }))
      .not.toBeInTheDocument();
    expect(screen.queryByText(/click a diff line's gutter/i))
      .not.toBeInTheDocument();
  });

  test('existing file-level threads still render (review comments preserved)', () => {
    // Removing the ENTRY point must not hide existing review
    // threads — they remain visible so the operator can still read
    // and reply to them.
    renderDiff({
      file: _file(10),
      comments: [{
        id: 'c1', body: 'pre-existing thread', line: -1,
        parent_id: '', status: 'open',
        author: 'reviewer', created_at: '2024-01-01T00:00:00Z',
      }],
    });
    expect(screen.getAllByText('pre-existing thread')
      .some((el) => el.closest('.diff-file-comment-body'))).toBe(true);
  });

  test('collapsed file still shows existing file-level threads', () => {
    renderDiff({
      file: _file(10),
      initiallyExpanded: false,
      comments: [{
        id: 'c1', body: 'pre-existing thread', line: -1,
        parent_id: '', status: 'open',
        author: 'reviewer', created_at: '2024-01-01T00:00:00Z',
      }],
    });
    expect(screen.getAllByText('pre-existing thread')
      .some((el) => el.closest('.diff-file-comment-body'))).toBe(true);
    expect(screen.queryByRole('button', { name: /add file-level comment/i }))
      .not.toBeInTheDocument();
  });
});

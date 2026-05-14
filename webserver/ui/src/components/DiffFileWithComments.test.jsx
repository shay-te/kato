// Component-level tests for DiffFileWithComments. The pure helpers
// (countDiffLines, isLargeFile, decideAutoExpand) already have unit
// tests; this file proves the React wiring:
//
//   - ``initiallyExpanded`` from ChangesTab drives the collapse state.
//   - When unspecified, the per-file fallback rule applies.
//   - The "Show diff (N lines)" button toggles the diff body.
//   - Collapsed files render a placeholder instead of the diff DOM.

import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import DiffFileWithComments from './DiffFileWithComments.jsx';
import { LARGE_FILE_LINE_THRESHOLD } from './diffFileSize.js';


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


function renderDiff({ file, ...rest } = {}) {
  return render(
    <DiffFileWithComments
      file={file || _file(10)}
      taskId="T1"
      repoId="repo-1"
      comments={[]}
      commentsLoading={false}
      commentsError=""
      onMutated={vi.fn()}
      onAddToChat={vi.fn()}
      {...rest}
    />,
  );
}


describe('DiffFileWithComments — collapse / expand integration', () => {

  test('initiallyExpanded=true: diff body renders inline', () => {
    const { container } = renderDiff({ file: _file(10), initiallyExpanded: true });

    // Expanded toggle: text is the minus glyph; title attr is "Hide diff".
    const toggle = container.querySelector('.diff-file-collapse-toggle');
    expect(toggle).toBeInTheDocument();
    expect(toggle).toHaveTextContent('−');
    expect(toggle).toHaveAttribute('title', expect.stringMatching(/hide diff/i));
    // The collapsed-placeholder text MUST NOT be present.
    expect(screen.queryByText(/diff hidden/i)).not.toBeInTheDocument();
  });

  test('initiallyExpanded=false: shows the "Show diff (N lines)" placeholder', () => {
    renderDiff({ file: _file(42), initiallyExpanded: false });

    // The collapse toggle button text reflects the line count.
    const toggle = screen.getByRole('button', { name: /show diff \(42 lines\)/i });
    expect(toggle).toBeInTheDocument();
    // The placeholder paragraph is present.
    expect(screen.getByText(/diff hidden/i)).toBeInTheDocument();
  });

  test('clicking the toggle expands a collapsed diff', () => {
    renderDiff({ file: _file(20), initiallyExpanded: false });

    expect(screen.getByText(/diff hidden/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /show diff/i }));
    expect(screen.queryByText(/diff hidden/i)).not.toBeInTheDocument();
  });

  test('clicking the toggle collapses an expanded diff', () => {
    const { container } = renderDiff({ file: _file(20), initiallyExpanded: true });

    expect(screen.queryByText(/diff hidden/i)).not.toBeInTheDocument();
    fireEvent.click(container.querySelector('.diff-file-collapse-toggle'));
    expect(screen.getByText(/diff hidden/i)).toBeInTheDocument();
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
    renderDiff({ file: _file(LARGE_FILE_LINE_THRESHOLD + 100) });
    expect(screen.getByText(/diff hidden/i)).toBeInTheDocument();
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
    renderDiff({ file: _file(20), initiallyExpanded: false });
    expect(screen.getByText(/diff hidden/i)).toBeInTheDocument();
  });
});


describe('DiffFileWithComments — header rendering', () => {

  test('renders the file path in the header', () => {
    renderDiff({ file: _file(10, { path: 'src/auth/login.py' }) });
    expect(screen.getByText('src/auth/login.py')).toBeInTheDocument();
  });

  test('shows the diff type chip (modify / add / delete)', () => {
    renderDiff({ file: _file(10, { type: 'add' }) });
    expect(screen.getByText('add')).toBeInTheDocument();
  });

  test('CONFLICTED badge shows when conflicted prop is true', () => {
    renderDiff({ file: _file(10), conflicted: true });
    expect(screen.getByText(/conflicted/i)).toBeInTheDocument();
  });

  test('CONFLICTED badge is absent by default', () => {
    renderDiff({ file: _file(10) });
    expect(screen.queryByText(/conflicted/i)).not.toBeInTheDocument();
  });
});


describe('DiffFileWithComments — file-level comment shortcut', () => {

  test('empty state shows the entry button, not an unrequested form', () => {
    // Operator-UX choice: we used to auto-open the form whenever a
    // file had zero comments, which planted an empty textarea +
    // Submit button under every clean file in the diff. Now the
    // form only appears when the operator explicitly clicks
    // "+ Add file-level comment" — clean files stay clean.
    renderDiff({ file: _file(10), comments: [] });
    expect(screen.queryByPlaceholderText(/add a file-level comment/i))
      .not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /add file-level comment/i }))
      .toBeInTheDocument();
  });

  test('clicking the entry button opens the file-level comment form', () => {
    renderDiff({ file: _file(10), comments: [] });
    fireEvent.click(
      screen.getByRole('button', { name: /add file-level comment/i }),
    );
    expect(screen.getByPlaceholderText(/add a file-level comment/i))
      .toBeInTheDocument();
    // Entry button is hidden once the form is open (so the operator
    // doesn't see two ways to do the same thing).
    expect(screen.queryByRole('button', { name: /add file-level comment/i }))
      .not.toBeInTheDocument();
  });

  test('with existing file-level threads, the entry button appears', () => {
    // The button is shown when there are existing threads so
    // adding more comments requires an explicit click — same rule
    // as the empty state above.
    renderDiff({
      file: _file(10),
      comments: [{
        id: 'c1', body: 'pre-existing thread', line: -1,
        parent_id: '', status: 'open',
        author: 'reviewer', created_at: '2024-01-01T00:00:00Z',
      }],
    });
    expect(screen.getByRole('button', { name: /add file-level comment/i }))
      .toBeInTheDocument();
  });
});

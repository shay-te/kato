/**
 * Markdown files render as prose, with a switch back to the source.
 *
 * Task-folder documents (``plan.md``, ``pr_description.md``,
 * ``resume_prompt.md``) are prose the agent wrote for the operator to READ —
 * they opened as raw Monaco text, headings and tables and all. They now
 * default to the rendered preview; a markdown file inside a repo is source
 * the agent is editing, so it still opens as source.
 */
import { describe, test, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor, cleanup } from '@testing-library/react';

vi.mock('@monaco-editor/react', () => ({
  default: ({ value }) => <div data-testid="monaco">{value}</div>,
}));

const MARKDOWN = '# Plan\n\n1. Do the thing\n\n| a | b |\n| - | - |\n| 1 | 2 |\n';

vi.mock('../api.js', () => ({
  fetchFileContent: vi.fn(async () => ({
    content: MARKDOWN, binary: false, too_large: false,
  })),
  fetchTaskComments: vi.fn(async () => ({ ok: true, body: { comments: [] } })),
  createTaskComment: vi.fn(),
  deleteTaskComment: vi.fn(),
  markTaskCommentAddressed: vi.fn(),
  reopenTaskComment: vi.fn(),
  resolveTaskComment: vi.fn(),
}));

vi.mock('../contexts/ChatComposerContext.jsx', () => ({
  useChatComposer: () => ({ appendToInput: vi.fn() }),
}));

import EditorPane from './EditorPane.jsx';

const TASK_FILE = {
  taskId: 'KATO-1',
  absolutePath: '/ws/KATO-1/plan.md',
  relativePath: 'plan.md',
  repoId: 'task files',
};
const REPO_FILE = {
  taskId: 'KATO-1',
  absolutePath: '/ws/KATO-1/repo/README.md',
  relativePath: 'README.md',
  repoId: 'repo',
};

afterEach(() => { cleanup(); });

describe('EditorPane markdown preview', () => {
  test('a task-folder .md renders as markdown, not raw text', async () => {
    const { container } = render(<EditorPane openFile={TASK_FILE} />);
    await waitFor(() => {
      expect(container.querySelector('.editor-pane-markdown')).toBeTruthy();
    });
    // Rendered: the heading is an <h1>, the table is a real <table>.
    expect(container.querySelector('h1').textContent).toBe('Plan');
    expect(container.querySelector('table')).toBeTruthy();
    // ...and Monaco is not mounted at all.
    expect(screen.queryByTestId('monaco')).toBeNull();
  });

  test('a repo .md opens as source', async () => {
    const { container } = render(<EditorPane openFile={REPO_FILE} />);
    await screen.findByTestId('monaco');
    expect(container.querySelector('.editor-pane-markdown')).toBeNull();
  });

  test('an explicit source choice on a task file wins', async () => {
    const { container } = render(
      <EditorPane openFile={{ ...TASK_FILE, mdView: 'source' }} />,
    );
    await screen.findByTestId('monaco');
    expect(container.querySelector('.editor-pane-markdown')).toBeNull();
  });

  test('an explicit preview choice on a repo file wins', async () => {
    const { container } = render(
      <EditorPane openFile={{ ...REPO_FILE, mdView: 'preview' }} />,
    );
    await waitFor(() => {
      expect(container.querySelector('.editor-pane-markdown')).toBeTruthy();
    });
    expect(screen.queryByTestId('monaco')).toBeNull();
  });

  test('a non-markdown task file is never previewed', async () => {
    const { container } = render(
      <EditorPane openFile={{ ...TASK_FILE, relativePath: 'notes.txt',
        absolutePath: '/ws/KATO-1/notes.txt' }} />,
    );
    await screen.findByTestId('monaco');
    expect(container.querySelector('.editor-pane-markdown')).toBeNull();
  });

  test('comments still render in preview mode', async () => {
    const api = await import('../api.js');
    api.fetchTaskComments.mockResolvedValueOnce({
      ok: true,
      body: {
        comments: [{
          id: 'c1', body: 'this section is wrong', line: 3,
          repo_id: 'task files', file_path: 'plan.md',
          status: 'open', author: 'operator', created_at: '2026-01-01T00:00:00Z',
        }],
      },
    });
    const { container } = render(<EditorPane openFile={TASK_FILE} />);
    await waitFor(() => {
      expect(container.querySelector('.editor-pane-markdown')).toBeTruthy();
    });
    // The threads live inside the preview, not in a Monaco view zone that
    // does not exist here — without this they would silently disappear.
    await waitFor(() => {
      expect(
        container.querySelector('.editor-pane-markdown .editor-pane-comments-wrap'),
      ).toBeTruthy();
    });
    // (CommentThread renders the body in more than one node — count, don't
    //  assume a single match.)
    expect(screen.getAllByText('this section is wrong').length).toBeGreaterThan(0);
  });
});

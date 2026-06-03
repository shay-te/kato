// Tests for EditorPane's file-path HEADER right-click menu.
//
// The Monaco editor body has its own native right-click menu (with the
// same "Copy relative path" action), but Monaco doesn't mount under
// jsdom — so here we stub @monaco-editor/react and exercise only the
// header menu, which is plain DOM. The shared copyRepoRelativePath
// helper is mocked so we can assert the exact (repoId, path) it copies.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// Monaco never mounts in jsdom; render a stub so EditorPane's body is
// inert and handleEditorMount (the editor-body menu) is irrelevant here.
vi.mock('@monaco-editor/react', () => ({ default: () => null }));

// No network: the pane fetches file content + comments on mount.
vi.mock('../api.js', () => ({
  fetchFileContent: vi.fn(async () => ({ content: '', binary: false, too_large: false })),
  fetchTaskComments: vi.fn(async () => ({ ok: true, body: { comments: [] } })),
  createTaskComment: vi.fn(),
  deleteTaskComment: vi.fn(),
  markTaskCommentAddressed: vi.fn(),
  reopenTaskComment: vi.fn(),
  resolveTaskComment: vi.fn(),
}));

// EditorPane only pulls appendToInput from the composer context.
vi.mock('../contexts/ChatComposerContext.jsx', () => ({
  useChatComposer: () => ({ appendToInput: vi.fn() }),
}));

// The thing under test calls this on "Copy relative path".
vi.mock('../utils/clipboard.js', () => ({
  copyRepoRelativePath: vi.fn(async () => {}),
}));

import EditorPane from './EditorPane.jsx';
import { copyRepoRelativePath } from '../utils/clipboard.js';

const OPEN_FILE = {
  taskId: 'KATO-1',
  absolutePath: '/ws/KATO-1/repo/src/foo.js',
  relativePath: 'src/foo.js',
  repoId: 'repo',
};

describe('EditorPane — header "Copy relative path" context menu', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  test('no menu until the header is right-clicked', () => {
    render(<EditorPane openFile={OPEN_FILE} />);
    expect(document.querySelector('.diff-file-context-menu')).toBeNull();
  });

  test('right-clicking the header opens a menu with "Copy relative path"', () => {
    const { container } = render(<EditorPane openFile={OPEN_FILE} />);
    fireEvent.contextMenu(container.querySelector('.editor-pane-header'));
    expect(document.querySelector('.diff-file-context-menu')).toBeInTheDocument();
    expect(
      screen.getByRole('menuitem', { name: /copy relative path/i }),
    ).toBeInTheDocument();
  });

  test('clicking it copies repo:relativePath via the shared helper, then closes', async () => {
    const { container } = render(<EditorPane openFile={OPEN_FILE} />);
    fireEvent.contextMenu(container.querySelector('.editor-pane-header'));
    fireEvent.click(screen.getByRole('menuitem', { name: /copy relative path/i }));
    // Same helper + arg order the Files tree and diff-file header use,
    // so a path copied from any surface is byte-identical.
    expect(copyRepoRelativePath).toHaveBeenCalledWith('repo', 'src/foo.js');
    await waitFor(() => {
      expect(document.querySelector('.diff-file-context-menu')).toBeNull();
    });
  });
});

// Tests that EditorPane renders NO path/read-only header strip.
//
// It used to carry one: file path + a permanent "read-only" pill + a
// "View diff" button, plus a right-click menu with the copy actions. All
// of it was redundant with the file tab directly above (which shows
// ``repoId/relativePath`` on hover and owns the diff ⇄ file toggle), and
// the pill was never conditional — the editor is always read-only — so
// the row cost a full line of vertical space to say nothing actionable.
//
// The copy actions moved into Monaco's own right-click menu
// (``kato.copyRelativePath`` / ``kato.copyFileName``). Monaco doesn't
// mount under jsdom, so those are not exercised here; this file only
// pins that the header does not come back.

import { describe, test, expect, vi } from 'vitest';
import { render } from '@testing-library/react';

// Monaco never mounts in jsdom; render a stub so the body is inert.
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

vi.mock('../contexts/ChatComposerContext.jsx', () => ({
  useChatComposer: () => ({ appendToInput: vi.fn() }),
}));

import EditorPane from './EditorPane.jsx';

const OPEN_FILE = {
  taskId: 'KATO-1',
  absolutePath: '/ws/KATO-1/repo/src/foo.js',
  relativePath: 'src/foo.js',
  repoId: 'repo',
};

describe('EditorPane — no path/read-only header strip', () => {
  test('renders no header row above the editor body', () => {
    const { container } = render(<EditorPane openFile={OPEN_FILE} />);
    expect(container.querySelector('.editor-pane-header')).toBeNull();
    expect(container.querySelector('.editor-pane-path')).toBeNull();
    expect(container.querySelector('.editor-pane-readonly-pill')).toBeNull();
  });

  test('does not repeat the file path that the tab already shows on hover', () => {
    const { container } = render(<EditorPane openFile={OPEN_FILE} />);
    expect(container.textContent).not.toContain('src/foo.js');
  });

  test('the editor body is the pane\'s first child', () => {
    const { container } = render(<EditorPane openFile={OPEN_FILE} />);
    const pane = container.querySelector('#editor-pane');
    expect(pane.firstElementChild).toHaveClass('editor-pane-body');
  });

  test('right-clicking the pane opens no custom menu (Monaco owns it)', () => {
    render(<EditorPane openFile={OPEN_FILE} />);
    expect(document.querySelector('.diff-file-context-menu')).toBeNull();
  });
});

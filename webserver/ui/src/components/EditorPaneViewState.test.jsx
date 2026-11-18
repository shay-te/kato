import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

const fakeEditorState = vi.hoisted(() => ({
  editor: null,
  scrollHandler: null,
  cursorHandler: null,
}));

vi.mock('@monaco-editor/react', async () => {
  const React = await vi.importActual('react');
  return {
    default: ({ onMount }) => {
      React.useEffect(() => {
        const editor = {
          addAction: vi.fn(),
          deltaDecorations: vi.fn(() => []),
          onMouseMove: vi.fn(),
          onMouseLeave: vi.fn(),
          onMouseDown: vi.fn(),
          onDidScrollChange: vi.fn((handler) => {
            fakeEditorState.scrollHandler = handler;
          }),
          onDidChangeCursorPosition: vi.fn((handler) => {
            fakeEditorState.cursorHandler = handler;
          }),
          saveViewState: vi.fn(() => ({ line: 77 })),
          restoreViewState: vi.fn(),
        };
        fakeEditorState.editor = editor;
        onMount(editor, {
          KeyCode: { KeyA: 1, KeyC: 2 },
          KeyMod: { CtrlCmd: 10, Shift: 20 },
          Range: function Range() {},
          editor: { MouseTargetType: { GUTTER_GLYPH_MARGIN: 1 } },
        });
      }, [onMount]);
      return <div data-testid="monaco-editor" />;
    },
  };
});

vi.mock('../api.js', () => ({
  fetchFileContent: vi.fn(async () => ({
    content: 'const answer = 42;\n',
    binary: false,
    too_large: false,
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

vi.mock('../utils/clipboard.js', () => ({
  copyRepoRelativePath: vi.fn(async () => {}),
}));

import EditorPane, { lineJumpDecoration } from './EditorPane.jsx';

const OPEN_FILE = {
  taskId: 'KATO-1',
  absolutePath: '/ws/KATO-1/repo/src/foo.js',
  relativePath: 'src/foo.js',
  repoId: 'repo',
};

describe('EditorPane — file view state', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fakeEditorState.editor = null;
    fakeEditorState.scrollHandler = null;
    fakeEditorState.cursorHandler = null;
  });

  test('restores the saved Monaco view state on mount', async () => {
    const saved = { line: 44 };
    render(<EditorPane openFile={{ ...OPEN_FILE, editorViewState: saved }} />);

    await screen.findByTestId('monaco-editor');
    await waitFor(() => {
      expect(fakeEditorState.editor.restoreViewState).toHaveBeenCalledWith(saved);
    });
  });

  test('reports Monaco scroll and cursor view state changes', async () => {
    const onViewStateChange = vi.fn();
    render(<EditorPane openFile={OPEN_FILE} onViewStateChange={onViewStateChange} />);

    await screen.findByTestId('monaco-editor');
    fakeEditorState.scrollHandler();
    fakeEditorState.cursorHandler();

    expect(onViewStateChange).toHaveBeenCalledWith({
      editorViewState: { line: 77 },
    });
    expect(onViewStateChange).toHaveBeenCalledTimes(2);
  });
});


// A content-search hit (or a go-to-definition) opens the file AT a line:
// scrolled there, and the line marked so it is plain which one was asked for.
describe('EditorPane — the line an opener jumped to', () => {
  test('is scrolled into the middle and marked as a whole line', async () => {
    render(<EditorPane openFile={{ ...OPEN_FILE, line: 1, openRequestId: 5 }} />);
    await waitFor(() => expect(fakeEditorState.editor?.deltaDecorations)
      .toHaveBeenCalledWith([], [lineJumpDecoration(1)]));
    expect(lineJumpDecoration(1)).toEqual({
      range: { startLineNumber: 1, startColumn: 1, endLineNumber: 1, endColumn: 1 },
      options: { isWholeLine: true, className: 'kato-line-jump', linesDecorationsClassName: 'kato-line-jump-gutter' },
    });
  });

  test('an open with no line marks nothing', async () => {
    render(<EditorPane openFile={{ ...OPEN_FILE, openRequestId: 6 }} />);
    await waitFor(() => expect(screen.getByTestId('monaco-editor')).toBeInTheDocument());
    expect(fakeEditorState.editor.deltaDecorations).not.toHaveBeenCalled();
  });
});

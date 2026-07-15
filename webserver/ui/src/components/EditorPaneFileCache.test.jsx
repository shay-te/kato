// Tests for EditorPane's file-content cache integration: a cached
// file shows INSTANTLY on re-open (no loading flash), but the fetch
// always still runs and sends the cached mtime back for the SERVER to
// verify — never trusting the client-side cache on its own. See
// utils/fileContentCache.js for why (a background branch sync, merge,
// or a direct edit outside kato can change a file with no SSE event
// the client would ever see).

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

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
          onDidScrollChange: vi.fn(),
          onDidChangeCursorPosition: vi.fn(),
          saveViewState: vi.fn(() => null),
          restoreViewState: vi.fn(),
        };
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

const fetchFileContent = vi.fn();
vi.mock('../api.js', () => ({
  fetchFileContent: (...args) => fetchFileContent(...args),
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

import EditorPane from './EditorPane.jsx';
import { _clearFileContentCacheForTests, writeCachedFileContent } from '../utils/fileContentCache.js';

const OPEN_FILE = {
  taskId: 'KATO-1',
  absolutePath: '/ws/KATO-1/repo/src/foo.js',
  relativePath: 'src/foo.js',
  repoId: 'repo',
};

describe('EditorPane — file content cache', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    _clearFileContentCacheForTests();
  });

  test('cache miss: fetches with no known_mtime and caches the result on success', async () => {
    fetchFileContent.mockResolvedValue({
      content: 'const x = 1;', binary: false, too_large: false, mtime: '111.0',
    });
    render(<EditorPane openFile={OPEN_FILE} />);

    await waitFor(() => {
      expect(fetchFileContent).toHaveBeenCalledWith(
        'KATO-1', '/ws/KATO-1/repo/src/foo.js', '',
      );
    });
    await screen.findByTestId('monaco-editor');
  });

  test('cache hit: shows the cached content immediately, no loading state', async () => {
    writeCachedFileContent('KATO-1', '/ws/KATO-1/repo/src/foo.js', {
      content: 'cached content', binary: false, tooLarge: false, mtime: '111.0',
    });
    // Never resolves during this test — proves the DISPLAY didn't wait on it.
    fetchFileContent.mockReturnValue(new Promise(() => {}));

    render(<EditorPane openFile={OPEN_FILE} />);

    // The Monaco stub mounts immediately — no "loading" gate blocked it,
    // which is only possible if state started as loading:false (from cache).
    await screen.findByTestId('monaco-editor');
  });

  test('cache hit still sends the cached mtime for server verification', async () => {
    writeCachedFileContent('KATO-1', '/ws/KATO-1/repo/src/foo.js', {
      content: 'cached content', binary: false, tooLarge: false, mtime: '111.0',
    });
    fetchFileContent.mockResolvedValue({ unchanged: true, mtime: '111.0' });

    render(<EditorPane openFile={OPEN_FILE} />);

    await waitFor(() => {
      expect(fetchFileContent).toHaveBeenCalledWith(
        'KATO-1', '/ws/KATO-1/repo/src/foo.js', '111.0',
      );
    });
  });

  test('server says unchanged: cached content is trusted, not overwritten', async () => {
    writeCachedFileContent('KATO-1', '/ws/KATO-1/repo/src/foo.js', {
      content: 'cached content', binary: false, tooLarge: false, mtime: '111.0',
    });
    fetchFileContent.mockResolvedValue({ unchanged: true, mtime: '111.0' });

    render(<EditorPane openFile={OPEN_FILE} />);
    await screen.findByTestId('monaco-editor');
    await waitFor(() => expect(fetchFileContent).toHaveBeenCalled());
  });

  test('server says the file changed: fresh content replaces the stale cache entry', async () => {
    // SAFETY case: something (a background merge, another process)
    // changed the file — the server's mtime no longer matches, so it
    // sends the real content back instead of `unchanged`.
    writeCachedFileContent('KATO-1', '/ws/KATO-1/repo/src/foo.js', {
      content: 'STALE', binary: false, tooLarge: false, mtime: '111.0',
    });
    fetchFileContent.mockResolvedValue({
      content: 'FRESH', binary: false, too_large: false, mtime: '222.0',
    });

    render(<EditorPane openFile={OPEN_FILE} />);
    await screen.findByTestId('monaco-editor');
    await waitFor(() => expect(fetchFileContent).toHaveBeenCalled());
    // The cache entry itself must now hold the fresh content + mtime,
    // so the NEXT open (e.g. a subsequent tab switch) uses it, not the
    // stale one.
    const { readCachedFileContent } = await import('../utils/fileContentCache.js');
    await waitFor(() => {
      const entry = readCachedFileContent('KATO-1', '/ws/KATO-1/repo/src/foo.js');
      expect(entry?.content).toBe('FRESH');
      expect(entry?.mtime).toBe('222.0');
    });
  });

  test('too_large responses are never cached', async () => {
    fetchFileContent.mockResolvedValue({ too_large: true, size: 5_000_000 });

    render(<EditorPane openFile={OPEN_FILE} />);
    await waitFor(() => expect(fetchFileContent).toHaveBeenCalled());

    const { readCachedFileContent } = await import('../utils/fileContentCache.js');
    expect(readCachedFileContent('KATO-1', '/ws/KATO-1/repo/src/foo.js')).toBeNull();
  });
});

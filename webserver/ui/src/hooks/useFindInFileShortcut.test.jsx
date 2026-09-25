// Ctrl/Cmd+F finds the highlighted word in the OPEN FILE.
//
// Operator report: "ctrl+f will do the search for the selected variable
// inside the file."
//
// Monaco already binds Ctrl+F when it has focus and seeds from its own
// selection, so this hook covers the other case: the operator highlighted
// something while the cursor was elsewhere (a diff row, the tree, a chat
// message). The browser's own find would search the RENDERED page, which for
// a virtualised editor is only the lines currently on screen — silently
// missing matches further down.

import { describe, test, expect, vi, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';

import { useFindInFileShortcut } from './useFindInFileShortcut.js';

function makeEditor() {
  const controller = { setSearchString: vi.fn() };
  const findAction = { run: vi.fn() };
  return {
    focus: vi.fn(),
    getContribution: vi.fn(() => controller),
    getAction: vi.fn(() => findAction),
    _controller: controller,
    _findAction: findAction,
  };
}

function Harness({ editorRef, enabled }) {
  useFindInFileShortcut(editorRef, { enabled });
  return <div data-testid="page">page</div>;
}

function press(key, init) {
  const event = new KeyboardEvent('keydown', {
    key, bubbles: true, cancelable: true, ...init,
  });
  window.dispatchEvent(event);
  return event;
}

function selectOnPage(text) {
  vi.spyOn(window, 'getSelection').mockReturnValue({ toString: () => text });
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); document.body.innerHTML = ''; });

describe('useFindInFileShortcut', () => {
  test('Ctrl+F seeds the find box with the selection and opens it', () => {
    const editor = makeEditor();
    selectOnPage('myVariable');
    render(<Harness editorRef={{ current: editor }} />);

    const event = press('f', { ctrlKey: true });

    expect(editor.focus).toHaveBeenCalled();
    expect(editor._controller.setSearchString).toHaveBeenCalledWith('myVariable');
    expect(editor._findAction.run).toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(true);
  });

  test('with nothing selected it just opens the find box', () => {
    const editor = makeEditor();
    selectOnPage('');
    render(<Harness editorRef={{ current: editor }} />);
    press('f', { ctrlKey: true });
    expect(editor._controller.setSearchString).not.toHaveBeenCalled();
    expect(editor._findAction.run).toHaveBeenCalled();
  });

  test('a press INSIDE the editor is left to Monaco', () => {
    // Monaco's own binding is better than anything reimplemented here, and
    // double-handling would fight it.
    const editor = makeEditor();
    selectOnPage('x');
    render(<Harness editorRef={{ current: editor }} />);
    const host = document.createElement('div');
    host.className = 'monaco-editor';
    const inner = document.createElement('span');
    host.appendChild(inner);
    document.body.appendChild(host);

    const event = new KeyboardEvent('keydown', {
      key: 'f', ctrlKey: true, bubbles: true, cancelable: true,
    });
    inner.dispatchEvent(event);

    expect(editor._findAction.run).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });

  test('with no file open the browser keeps Ctrl+F', () => {
    render(<Harness editorRef={{ current: null }} />);
    const event = press('f', { ctrlKey: true });
    expect(event.defaultPrevented).toBe(false);
  });

  test('Ctrl+Shift+F is left alone — that is the global search', () => {
    const editor = makeEditor();
    render(<Harness editorRef={{ current: editor }} />);
    const event = press('f', { ctrlKey: true, shiftKey: true });
    expect(editor._findAction.run).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });

  test('a Monaco rename degrades to "opens empty", not a crash', () => {
    // getContribution / actions.find are Monaco-internal ids.
    const editor = makeEditor();
    editor.getContribution = vi.fn(() => { throw new Error('renamed'); });
    selectOnPage('x');
    render(<Harness editorRef={{ current: editor }} />);
    expect(() => press('f', { ctrlKey: true })).not.toThrow();
  });

  test('the listener is removed on unmount', () => {
    const editor = makeEditor();
    const { unmount } = render(<Harness editorRef={{ current: editor }} />);
    unmount();
    press('f', { ctrlKey: true });
    expect(editor._findAction.run).not.toHaveBeenCalled();
  });
});

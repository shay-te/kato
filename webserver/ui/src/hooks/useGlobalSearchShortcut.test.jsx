// Ctrl/Cmd+Shift+F searches the task's repos for the highlighted word.
//
// Operator report: "when I highlight a function name or a variable name we do
// ctrl+shift+f and it opens up the task search, I don't want this — I want him
// to search my selected word on the global finder on the left side."
//
// So this key now seeds the Files pane's content search. The task palette it
// used to open moved to Ctrl+Shift+P.

import { describe, test, expect, vi, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';

import { useGlobalSearchShortcut } from './useGlobalSearchShortcut.js';

function Harness({ onSearch, getSeed }) {
  useGlobalSearchShortcut(onSearch, getSeed);
  return <input aria-label="composer" />;
}

function press(key, init) {
  const event = new KeyboardEvent('keydown', {
    key, bubbles: true, cancelable: true, ...init,
  });
  window.dispatchEvent(event);
  return event;
}

afterEach(() => { cleanup(); document.body.innerHTML = ''; });

describe('useGlobalSearchShortcut', () => {
  test('Ctrl+Shift+F searches for the highlighted word', () => {
    const onSearch = vi.fn();
    render(<Harness onSearch={onSearch} getSeed={() => 'project_list'} />);
    const event = press('f', { ctrlKey: true, shiftKey: true });
    expect(onSearch).toHaveBeenCalledWith('project_list');
    // Claimed, so the browser's own find-variants cannot also fire.
    expect(event.defaultPrevented).toBe(true);
  });

  test('Cmd+Shift+F works on macOS', () => {
    const onSearch = vi.fn();
    render(<Harness onSearch={onSearch} getSeed={() => 'myVar'} />);
    press('f', { metaKey: true, shiftKey: true });
    expect(onSearch).toHaveBeenCalledWith('myVar');
  });

  test('with nothing selected it still opens the search, empty', () => {
    // Opening an empty search is the useful answer; refusing the gesture
    // because nothing is highlighted would just look broken.
    const onSearch = vi.fn();
    render(<Harness onSearch={onSearch} getSeed={() => ''} />);
    press('f', { ctrlKey: true, shiftKey: true });
    expect(onSearch).toHaveBeenCalledWith('');
  });

  test('a seed getter that throws does not swallow the gesture', () => {
    const onSearch = vi.fn();
    render(<Harness onSearch={onSearch} getSeed={() => { throw new Error('x'); }} />);
    press('f', { ctrlKey: true, shiftKey: true });
    expect(onSearch).toHaveBeenCalledWith('');
  });

  test('Ctrl+F alone is left alone — that is find-in-file', () => {
    const onSearch = vi.fn();
    render(<Harness onSearch={onSearch} getSeed={() => 'x'} />);
    const event = press('f', { ctrlKey: true });
    expect(onSearch).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });

  test('Ctrl+Shift+P is left alone — that is the task palette', () => {
    const onSearch = vi.fn();
    render(<Harness onSearch={onSearch} getSeed={() => 'x'} />);
    press('p', { ctrlKey: true, shiftKey: true });
    expect(onSearch).not.toHaveBeenCalled();
  });

  test('a bare f still types', () => {
    const onSearch = vi.fn();
    render(<Harness onSearch={onSearch} getSeed={() => 'x'} />);
    const event = press('f', {});
    expect(onSearch).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });

  test('it fires while focus is in a text field', () => {
    // Same reasoning as the palette: the composer is where the cursor lives,
    // and Ctrl/Cmd+Shift held cannot be mistaken for typing.
    const onSearch = vi.fn();
    const { getByLabelText } = render(
      <Harness onSearch={onSearch} getSeed={() => 'x'} />,
    );
    getByLabelText('composer').focus();
    press('f', { ctrlKey: true, shiftKey: true });
    expect(onSearch).toHaveBeenCalledTimes(1);
  });

  test('it stands down while a modal owns the keyboard', () => {
    const onSearch = vi.fn();
    render(<Harness onSearch={onSearch} getSeed={() => 'x'} />);
    const dialog = document.createElement('div');
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    document.body.appendChild(dialog);
    press('f', { ctrlKey: true, shiftKey: true });
    expect(onSearch).not.toHaveBeenCalled();
  });

  test('the listener is removed on unmount', () => {
    const onSearch = vi.fn();
    const { unmount } = render(
      <Harness onSearch={onSearch} getSeed={() => 'x'} />,
    );
    unmount();
    press('f', { ctrlKey: true, shiftKey: true });
    expect(onSearch).not.toHaveBeenCalled();
  });
});

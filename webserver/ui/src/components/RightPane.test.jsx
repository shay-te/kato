// Tests for RightPane. Hosts the workspace file tree when a task is
// selected, or a no-task placeholder when nothing is. The
// Files/Changes tab toggle (and the Changes tab) were removed — the
// file tree is now the only view. Cmd+P (or Ctrl+P) bumps a focus
// signal FilesTab listens to.
//
// FilesTab pulls in api.js + a context provider and fires real
// fetches; we replace it with a trivial stub so this test focuses
// on RightPane's own behavior (layout, keyboard shortcut, no-task
// branch).

import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

// Stub the heavy child BEFORE importing RightPane.
vi.mock('../FilesTab.jsx', () => ({
  default: ({ taskId, focusFilterSignal }) => (
    <div data-testid="files-tab" data-task-id={taskId} data-focus-signal={focusFilterSignal}>
      FilesTabStub
    </div>
  ),
}));

import RightPane from './RightPane.jsx';


describe('RightPane', () => {

  test('renders the no-task placeholder when there is no active task', () => {
    render(<RightPane activeTaskId={null} />);
    expect(screen.getByText(/No task selected/i)).toBeInTheDocument();
    // No file tree, and (since the toggle was removed) no tab chrome.
    expect(screen.queryByTestId('files-tab')).not.toBeInTheDocument();
    expect(screen.queryByText('Changes')).not.toBeInTheDocument();
  });

  test('renders the file tree directly when there is an active task', () => {
    render(<RightPane activeTaskId="KATO-1" />);
    expect(screen.getByTestId('files-tab')).toBeInTheDocument();
    // The Files/Changes toggle was removed entirely — no "Changes".
    expect(screen.queryByText('Changes')).not.toBeInTheDocument();
  });

  test('forwards the active task id to FilesTab', () => {
    render(<RightPane activeTaskId="KATO-1" />);
    expect(screen.getByTestId('files-tab').getAttribute('data-task-id'))
      .toBe('KATO-1');
  });

  test('width prop is applied as inline style on the aside', () => {
    const { container } = render(<RightPane activeTaskId="KATO-1" width={420} />);
    const aside = container.querySelector('#right-pane');
    // React inlines numeric width as a CSS pixel value.
    expect(aside.style.width).toBe('420px');
  });

  test('left-pane resizer renders when an onResizePointerDown is passed', () => {
    // The file-tree panel is the LEFT column in the top-tabs layout;
    // its drag handle is #left-pane-resizer and only mounts when the
    // resize callback is wired (App passes leftResizer).
    const { container } = render(
      <RightPane activeTaskId="KATO-1" onResizePointerDown={() => {}} />,
    );
    expect(container.querySelector('#left-pane-resizer')).toBeInTheDocument();
  });

  test('Cmd+P with an active task bumps the focus signal', () => {
    render(<RightPane activeTaskId="KATO-1" />);
    const before = screen.getByTestId('files-tab');
    expect(before.getAttribute('data-focus-signal')).toBe('0');

    fireEvent.keyDown(window, { key: 'p', metaKey: true });
    // focusFilterSignal starts at 0; one Cmd+P bumps it to 1.
    expect(screen.getByTestId('files-tab').getAttribute('data-focus-signal'))
      .toBe('1');
  });

  test('non-Cmd+P keystrokes do not bump the focus signal', () => {
    // Exercises the keydown guard early-returns: a plain key, a
    // non-P meta combo, and a Cmd+Shift+P modifier combo must all
    // leave the focus signal untouched.
    render(<RightPane activeTaskId="KATO-1" />);
    fireEvent.keyDown(window, { key: 'a' });
    fireEvent.keyDown(window, { key: 's', metaKey: true });
    fireEvent.keyDown(window, { key: 'p', metaKey: true, shiftKey: true });
    expect(screen.getByTestId('files-tab').getAttribute('data-focus-signal'))
      .toBe('0');
  });

  test('Cmd+P with no active task does NOT intercept the shortcut', () => {
    // When there's no active task, the effect is short-circuited
    // (it returns before attaching a listener). The shortcut passes
    // through and no file tree materializes.
    render(<RightPane activeTaskId={null} />);
    fireEvent.keyDown(window, { key: 'p', metaKey: true });
    expect(screen.queryByTestId('files-tab')).not.toBeInTheDocument();
  });
});

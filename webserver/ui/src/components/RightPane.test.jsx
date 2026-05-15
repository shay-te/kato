// Tests for RightPane. Hosts the Files / Changes tabs when a task
// is selected, or the OrchestratorActivityFeed when nothing is.
// Cmd+P (or Ctrl+P) flips to Files and bumps a focus signal.
//
// FilesTab and ChangesTab pull in api.js + a context provider and
// fire real fetches; we replace them with trivial stubs so this
// test focuses on RightPane's own behavior (tab switching, layout,
// keyboard shortcut, no-task branch).

import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

// Stub heavy children BEFORE importing RightPane.
vi.mock('../FilesTab.jsx', () => ({
  default: ({ taskId, focusFilterSignal }) => (
    <div data-testid="files-tab" data-task-id={taskId} data-focus-signal={focusFilterSignal}>
      FilesTabStub
    </div>
  ),
}));
vi.mock('../ChangesTab.jsx', () => ({
  default: ({ taskId }) => (
    <div data-testid="changes-tab" data-task-id={taskId}>
      ChangesTabStub
    </div>
  ),
}));

import RightPane from './RightPane.jsx';


describe('RightPane', () => {

  test('renders the no-task placeholder when there is no active task', () => {
    render(<RightPane activeTaskId={null} />);
    // The orchestrator feed moved OUT of the left pane — it now
    // opens in the centre column via the header status pill. With
    // no task the left pane shows a placeholder, not the feed.
    expect(screen.getByText(/No task selected/i)).toBeInTheDocument();
    expect(screen.queryByText('orchestrator activity')).not.toBeInTheDocument();
    // Tabs not rendered when no task is selected.
    expect(screen.queryByText('Files')).not.toBeInTheDocument();
    expect(screen.queryByText('Changes')).not.toBeInTheDocument();
  });

  test('renders the Files/Changes tabs when there is an active task', () => {
    render(<RightPane activeTaskId="KATO-1" />);
    expect(screen.getByText('Files')).toBeInTheDocument();
    expect(screen.getByText('Changes')).toBeInTheDocument();
    // Files tab is the default → its body is mounted.
    expect(screen.getByTestId('files-tab')).toBeInTheDocument();
    expect(screen.queryByTestId('changes-tab')).not.toBeInTheDocument();
  });

  test('clicking Changes switches the body to ChangesTab', () => {
    render(<RightPane activeTaskId="KATO-1" />);
    fireEvent.click(screen.getByText('Changes'));
    expect(screen.getByTestId('changes-tab')).toBeInTheDocument();
    expect(screen.queryByTestId('files-tab')).not.toBeInTheDocument();
  });

  test('clicking the active Files tab while already on Files keeps it active', () => {
    render(<RightPane activeTaskId="KATO-1" />);
    fireEvent.click(screen.getByText('Files'));
    expect(screen.getByTestId('files-tab')).toBeInTheDocument();
  });

  test('width prop is applied as inline style on the aside', () => {
    const { container } = render(<RightPane activeTaskId="KATO-1" width={420} />);
    const aside = container.querySelector('#right-pane');
    // React inlines numeric width as a CSS pixel value.
    expect(aside.style.width).toBe('420px');
  });

  test('left-pane resizer renders when an onResizePointerDown is passed', () => {
    // The Files/Changes panel is the LEFT column in the top-tabs
    // layout; its drag handle is #left-pane-resizer and only mounts
    // when the resize callback is wired (App passes leftResizer).
    const { container } = render(
      <RightPane activeTaskId="KATO-1" onResizePointerDown={() => {}} />,
    );
    expect(container.querySelector('#left-pane-resizer')).toBeInTheDocument();
  });

  test('Cmd+P with an active task switches to Files and bumps focus signal', () => {
    render(<RightPane activeTaskId="KATO-1" />);
    // Start on Changes so we can verify the keyboard shortcut flips
    // back to Files.
    fireEvent.click(screen.getByText('Changes'));
    expect(screen.getByTestId('changes-tab')).toBeInTheDocument();

    fireEvent.keyDown(window, { key: 'p', metaKey: true });
    const filesTab = screen.getByTestId('files-tab');
    expect(filesTab).toBeInTheDocument();
    // focusFilterSignal starts at 0; one Cmd+P bumps it to 1.
    expect(filesTab.getAttribute('data-focus-signal')).toBe('1');
  });

  test('Cmd+P with no active task does NOT intercept the shortcut', () => {
    // When there's no active task, the effect is short-circuited
    // (it returns before attaching a listener). The shortcut should
    // pass through. We can't observe browser default print here,
    // but we can at least verify the right pane still shows the
    // activity feed (no tab body materializes).
    render(<RightPane activeTaskId={null} activityHistory={[]} />);
    fireEvent.keyDown(window, { key: 'p', metaKey: true });
    expect(screen.queryByTestId('files-tab')).not.toBeInTheDocument();
  });
});

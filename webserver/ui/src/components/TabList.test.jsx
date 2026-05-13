// Tests for TabList. Maps sessions to Tabs in a <ul> and renders
// the header buttons (Add task, Scan now). Empty state shows when
// the sessions list is empty.

import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import TabList from './TabList.jsx';
import { TAB_STATUS } from '../constants/tabStatus.js';


function _session(taskId, overrides = {}) {
  return {
    task_id: taskId,
    task_summary: `Summary ${taskId}`,
    status: TAB_STATUS.ACTIVE,
    working: false,
    live: true,
    claude_session_id: 'sess',
    ...overrides,
  };
}


describe('TabList', () => {

  test('renders each session as a Tab', () => {
    render(
      <TabList
        sessions={[_session('A-1'), _session('A-2'), _session('A-3')]}
        activeTaskId="A-2"
        onSelect={() => {}}
      />,
    );
    expect(screen.getByText('A-1')).toBeInTheDocument();
    expect(screen.getByText('A-2')).toBeInTheDocument();
    expect(screen.getByText('A-3')).toBeInTheDocument();
  });

  test('marks the activeTaskId tab as active', () => {
    const { container } = render(
      <TabList
        sessions={[_session('A-1'), _session('A-2')]}
        activeTaskId="A-2"
        onSelect={() => {}}
      />,
    );
    const tabs = container.querySelectorAll('li.tab');
    expect(tabs[0]).not.toHaveClass('active');
    expect(tabs[1]).toHaveClass('active');
  });

  test('marks tabs in attentionTaskIds as needs-attention', () => {
    const { container } = render(
      <TabList
        sessions={[_session('A-1'), _session('A-2')]}
        attentionTaskIds={new Set(['A-1'])}
        onSelect={() => {}}
      />,
    );
    const tabs = container.querySelectorAll('li.tab');
    expect(tabs[0]).toHaveClass('needs-attention');
    expect(tabs[1]).not.toHaveClass('needs-attention');
  });

  test('renders the empty-state copy when sessions is empty', () => {
    render(<TabList sessions={[]} onSelect={() => {}} />);
    expect(screen.getByText(/No tabs yet/)).toBeInTheDocument();
    // "+ Add task" appears as a strong inside the empty-state copy
    // (separate from the header button). Use the empty-state id to
    // disambiguate from the button's aria-label.
    expect(screen.getByText('+ Add task')).toBeInTheDocument();
  });

  test('renders the empty-state when sessions is undefined (defensive)', () => {
    render(<TabList sessions={undefined} onSelect={() => {}} />);
    expect(screen.getByText(/No tabs yet/)).toBeInTheDocument();
  });

  test('Add task button fires onOpenAddTask', () => {
    const onOpenAddTask = vi.fn();
    render(<TabList sessions={[]} onSelect={() => {}} onOpenAddTask={onOpenAddTask} />);
    fireEvent.click(screen.getByLabelText('Add a task'));
    expect(onOpenAddTask).toHaveBeenCalledTimes(1);
  });

  test('Scan now button fires onScanNow when enabled', () => {
    const onScanNow = vi.fn();
    render(
      <TabList sessions={[]} onSelect={() => {}} onScanNow={onScanNow} scanPending={false} />,
    );
    fireEvent.click(screen.getByLabelText('Scan now'));
    expect(onScanNow).toHaveBeenCalledTimes(1);
  });

  test('Scan now button is disabled while scanPending is true', () => {
    render(
      <TabList sessions={[]} onSelect={() => {}} onScanNow={() => {}} scanPending={true} />,
    );
    expect(screen.getByLabelText('Scan now')).toBeDisabled();
  });

  test('Scan now button is disabled when onScanNow is not provided', () => {
    render(<TabList sessions={[]} onSelect={() => {}} />);
    expect(screen.getByLabelText('Scan now')).toBeDisabled();
  });
});

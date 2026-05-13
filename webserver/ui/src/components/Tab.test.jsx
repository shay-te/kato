// Tests for Tab. Renders one task tab in the sidebar list: task id,
// summary, status dot, optional commit indicator, forget (X) button.
// onSelect fires on click; onForget fires on X click (after a
// window.confirm). The active prop drives styling.

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import Tab from './Tab.jsx';
import { TAB_STATUS } from '../constants/tabStatus.js';


function _session(overrides = {}) {
  return {
    task_id: 'KATO-123',
    task_summary: 'Fix the bug',
    status: TAB_STATUS.ACTIVE,
    working: true,
    has_changes_pending: false,
    live: true,
    claude_session_id: 'sess-1',
    ...overrides,
  };
}


describe('Tab', () => {

  test('renders the task id and summary', () => {
    render(<Tab session={_session()} onSelect={() => {}} />);
    expect(screen.getByText('KATO-123')).toBeInTheDocument();
    expect(screen.getByText('Fix the bug')).toBeInTheDocument();
  });

  test('clicking the tab fires onSelect with the task id', () => {
    const onSelect = vi.fn();
    render(<Tab session={_session()} onSelect={onSelect} />);
    fireEvent.click(screen.getByText('KATO-123'));
    expect(onSelect).toHaveBeenCalledWith('KATO-123');
  });

  test('active prop adds the active class', () => {
    const { container } = render(
      <Tab session={_session()} active={true} onSelect={() => {}} />,
    );
    expect(container.querySelector('li')).toHaveClass('active');
  });

  test('needsAttention prop adds the needs-attention class', () => {
    const { container } = render(
      <Tab session={_session()} needsAttention={true} onSelect={() => {}} />,
    );
    expect(container.querySelector('li')).toHaveClass('needs-attention');
  });

  test('status dot reflects the resolved status (attention overrides base)', () => {
    const { container } = render(
      <Tab session={_session({ status: TAB_STATUS.ACTIVE })} needsAttention={true} onSelect={() => {}} />,
    );
    // resolveTabStatus → ATTENTION when needsAttention is true.
    expect(container.querySelector('.status-dot')).toHaveClass(`status-${TAB_STATUS.ATTENTION}`);
  });

  test('changes-pending indicator appears only when has_changes_pending is true', () => {
    const { container: c1 } = render(
      <Tab session={_session({ has_changes_pending: false })} onSelect={() => {}} />,
    );
    expect(c1.querySelector('.tab-changes-indicator')).toBeNull();

    const { container: c2 } = render(
      <Tab session={_session({ has_changes_pending: true })} onSelect={() => {}} />,
    );
    expect(c2.querySelector('.tab-changes-indicator')).toBeInTheDocument();
  });

  test('clicking forget button asks confirm then fires onForget(task_id)', () => {
    const onSelect = vi.fn();
    const onForget = vi.fn();
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<Tab session={_session()} onSelect={onSelect} onForget={onForget} />);

    fireEvent.click(screen.getByLabelText('Forget this task'));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(onForget).toHaveBeenCalledWith('KATO-123');
    // event.stopPropagation in handleForget — onSelect must not fire.
    expect(onSelect).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  test('forget button does nothing when user cancels the confirm', () => {
    const onForget = vi.fn();
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<Tab session={_session()} onSelect={() => {}} onForget={onForget} />);

    fireEvent.click(screen.getByLabelText('Forget this task'));
    expect(onForget).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  test('forget button is a no-op when onForget is not a function', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<Tab session={_session()} onSelect={() => {}} />);
    // Should not throw, should not even ask confirm — handleForget
    // bails when typeof onForget !== 'function'.
    fireEvent.click(screen.getByLabelText('Forget this task'));
    expect(confirmSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  test('renders empty summary when task_summary missing without crashing', () => {
    const { container } = render(
      <Tab session={_session({ task_summary: null })} onSelect={() => {}} />,
    );
    const p = container.querySelector('p');
    expect(p).toBeInTheDocument();
    expect(p.textContent).toBe('');
  });
});

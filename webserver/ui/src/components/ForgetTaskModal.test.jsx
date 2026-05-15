// Tests for ForgetTaskModal — the hard-confirm dialog that replaced
// the native window.confirm on the tab "X". Pins: nothing renders
// without a session; consequences are always listed; the danger
// button must be clicked to confirm; Cancel / Esc / backdrop all
// cancel; the in-review and un-pushed-changes red callouts are
// context-driven.

import { describe, test, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import ForgetTaskModal from './ForgetTaskModal.jsx';

function _session(overrides = {}) {
  return { task_id: 'KATO-123', status: 'active', ...overrides };
}

describe('ForgetTaskModal', () => {
  test('renders nothing without a session', () => {
    const { container } = render(
      <ForgetTaskModal session={null} onConfirm={vi.fn()} onCancel={vi.fn()} />,
    );
    expect(container.firstChild).toBeNull();
  });

  test('shows the task id and the consequence list', () => {
    render(
      <ForgetTaskModal
        session={_session()} onConfirm={vi.fn()} onCancel={vi.fn()}
      />,
    );
    expect(screen.getByRole('dialog')).toHaveAttribute('aria-modal', 'true');
    expect(document.getElementById('forget-task-name')).toHaveTextContent(
      'KATO-123',
    );
    const effects = document.querySelectorAll('.forget-task-effects li');
    expect(effects.length).toBe(4);
    expect(
      screen.getByText(/not already pushed to a pull/i),
    ).toBeInTheDocument();
  });

  test('confirm button is a distinct danger action and fires onConfirm', () => {
    const onConfirm = vi.fn();
    render(
      <ForgetTaskModal
        session={_session()} onConfirm={onConfirm} onCancel={vi.fn()}
      />,
    );
    const confirm = document.getElementById('forget-task-confirm');
    expect(confirm).toHaveClass('danger');
    expect(confirm).toHaveTextContent('Forget KATO-123');
    fireEvent.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  test('Cancel fires onCancel and is the auto-focused default', () => {
    const onCancel = vi.fn();
    render(
      <ForgetTaskModal
        session={_session()} onConfirm={vi.fn()} onCancel={onCancel}
      />,
    );
    const cancel = document.getElementById('forget-task-cancel');
    expect(cancel).toHaveFocus();
    fireEvent.click(cancel);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  test('Escape cancels', () => {
    const onCancel = vi.fn();
    render(
      <ForgetTaskModal
        session={_session()} onConfirm={vi.fn()} onCancel={onCancel}
      />,
    );
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  test('clicking the backdrop cancels, clicking the card does not', () => {
    const onCancel = vi.fn();
    render(
      <ForgetTaskModal
        session={_session()} onConfirm={vi.fn()} onCancel={onCancel}
      />,
    );
    fireEvent.click(document.querySelector('.modal-card'));  // inside the card
    expect(onCancel).not.toHaveBeenCalled();
    fireEvent.click(document.getElementById('forget-task-modal'));  // backdrop
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  test('no red callout for a plain active task', () => {
    render(
      <ForgetTaskModal
        session={_session()} onConfirm={vi.fn()} onCancel={vi.fn()}
      />,
    );
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  test('in-review task gets the "To Verify" red warning', () => {
    render(
      <ForgetTaskModal
        session={_session({ status: 'review' })}
        onConfirm={vi.fn()} onCancel={vi.fn()}
      />,
    );
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent(/in review/i);
    expect(alert).toHaveTextContent(/To Verify/i);
  });

  test('un-pushed changes get their own red warning', () => {
    render(
      <ForgetTaskModal
        session={_session({ has_changes_pending: true })}
        onConfirm={vi.fn()} onCancel={vi.fn()}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent(
      /haven.t been pushed yet/i,
    );
  });

  test('both warnings combine when in review AND changes pending', () => {
    render(
      <ForgetTaskModal
        session={_session({ status: 'review', has_changes_pending: true })}
        onConfirm={vi.fn()} onCancel={vi.fn()}
      />,
    );
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent(/in review/i);
    expect(alert).toHaveTextContent(/haven.t been pushed yet/i);
  });

  test('falls back to "this task" when the id is blank', () => {
    render(
      <ForgetTaskModal
        session={{ task_id: '   ' }} onConfirm={vi.fn()} onCancel={vi.fn()}
      />,
    );
    expect(
      document.getElementById('forget-task-confirm'),
    ).toHaveTextContent('Forget this task');
  });
});

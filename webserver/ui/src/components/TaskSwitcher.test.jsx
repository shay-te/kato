// The Cmd+Tab-style switcher: while Tab / Ctrl+Tab are HELD, the tasks show in
// the middle of the screen, the one landed on highlighted (when it shows is the
// hook's: useTaskTabShortcuts.hook.test.jsx).

import { describe, test, expect, afterEach } from 'vitest';
import { act, render, screen } from '@testing-library/react';

import TaskSwitcher from './TaskSwitcher.jsx';
import { setTaskSwitcherShown } from '../stores/taskSwitchStore.js';

const session = (task_id, extra = {}) => ({ task_id, task_summary: `name of ${task_id}`, status: 'active', ...extra });
const SESSIONS = [
  session('B-2'),
  session('UNA-1', { task_summary: 'checkout redesign' }),
  session('UNA-7', { task_summary: 'Subtask of UNA-1: the payment page' }),
];
const ORDER = ['UNA-1', 'UNA-7', 'B-2'];

afterEach(() => { act(() => setTaskSwitcherShown(false)); });

function renderOn(activeTaskId, extra = {}) {
  return render(
    <TaskSwitcher sessions={SESSIONS} order={ORDER} activeTaskId={activeTaskId} {...extra} />,
  );
}

describe('TaskSwitcher', () => {
  test('nothing on screen until a press is held', () => {
    const { container } = renderOn('UNA-1');
    expect(container.querySelector('.task-switcher')).toBeNull();
  });

  test('held, it shows every task in the menu\'s order, the one landed on highlighted', () => {
    const { container } = renderOn('UNA-7');
    act(() => setTaskSwitcherShown(true));
    const items = [...container.querySelectorAll('.task-switcher-item')];
    expect(items.map((li) => li.dataset.taskId)).toEqual(ORDER);
    expect(items[1]).toHaveClass('is-active');
    expect(items[1]).toHaveClass('is-nested');  // a sub-task, under its main task
    expect(items[1]).toHaveTextContent('the payment page');  // without "Subtask of UNA-1:"
    expect(screen.getByRole('status')).toHaveAttribute('aria-label', 'On UNA-7');
    // Each dot is the shared derivation's own class.
    expect(items[0].querySelector('.status-dot')).not.toBeNull();
  });

  test('let go, it goes; told twice, nothing changes', () => {
    const { container } = renderOn('UNA-1');
    act(() => { setTaskSwitcherShown(true); setTaskSwitcherShown(true); });
    expect(container.querySelector('.task-switcher')).not.toBeNull();
    act(() => setTaskSwitcherShown(false));
    expect(container.querySelector('.task-switcher')).toBeNull();
  });

  test('a long list shows a window around the task landed on', () => {
    const many = Array.from({ length: 20 }, (_, i) => session(`T-${i}`));
    const { container } = render(
      <TaskSwitcher sessions={many} order={many.map((s) => s.task_id)} activeTaskId="T-15" />,
    );
    act(() => setTaskSwitcherShown(true));
    const ids = [...container.querySelectorAll('.task-switcher-item')].map((li) => li.dataset.taskId);
    expect(ids).toHaveLength(9);
    expect(ids).toContain('T-15');
    expect(ids[0]).toBe('T-11');
  });
});

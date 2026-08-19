// The Ctrl/Cmd+P "go to task" palette.
//
// Tab/Shift+Tab already steps the task strip, but that only helps when the
// task is a step or two away. With a full strip — most of it scrolled out
// of sight — the only way to reach a task was to scroll and read every
// pill. This is the "I know its name, take me there" path.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import TaskPalette from './TaskPalette.jsx';

const S = (task_id, task_summary = '') => ({ task_id, task_summary });

const SESSIONS = [
  S('UNA-2818', 'elastic search variables'),
  S('UNA-1200', 'fix the login redirect'),
  S('ABC-7', 'payments rewrite'),
];

function renderPalette(props = {}) {
  const onSelect = props.onSelect || vi.fn();
  const onClose = props.onClose || vi.fn();
  render(
    <TaskPalette
      sessions={props.sessions || SESSIONS}
      nameFor={props.nameFor || (() => '')}
      onSelect={onSelect}
      onClose={onClose}
    />,
  );
  return { onSelect, onClose };
}

const input = () => screen.getByRole('combobox', { name: /search tasks/i });
const rows = () => screen.getAllByRole('option');
const rowLabels = () => rows().map((row) => row.textContent);

describe('TaskPalette', () => {
  test('lists every task before anything is typed', () => {
    renderPalette();
    expect(rows()).toHaveLength(3);
  });

  test('the search box is focused on open, so the shortcut is one gesture', () => {
    renderPalette();
    expect(document.activeElement).toBe(input());
  });

  test('typing narrows the list', () => {
    renderPalette();
    fireEvent.change(input(), { target: { value: 'elastic' } });
    expect(rowLabels()).toEqual([expect.stringContaining('UNA-2818')]);
  });

  test('clicking a row opens that task and closes the palette', () => {
    const { onSelect, onClose } = renderPalette();
    fireEvent.click(screen.getByText('ABC-7'));
    expect(onSelect).toHaveBeenCalledWith('ABC-7');
    expect(onClose).toHaveBeenCalled();
  });

  test('Enter opens the highlighted row', () => {
    const { onSelect } = renderPalette();
    fireEvent.keyDown(input(), { key: 'Enter' });
    expect(onSelect).toHaveBeenCalledWith('UNA-2818');
  });

  test('arrows move the highlight and Enter follows it', () => {
    const { onSelect } = renderPalette();
    fireEvent.keyDown(input(), { key: 'ArrowDown' });
    fireEvent.keyDown(input(), { key: 'Enter' });
    expect(onSelect).toHaveBeenCalledWith('UNA-1200');
  });

  test('ArrowUp from the top wraps to the bottom', () => {
    // Wrapping, not clamping — stopping dead while a key is held reads
    // as the palette having frozen.
    const { onSelect } = renderPalette();
    fireEvent.keyDown(input(), { key: 'ArrowUp' });
    fireEvent.keyDown(input(), { key: 'Enter' });
    expect(onSelect).toHaveBeenCalledWith('ABC-7');
  });

  test('Escape closes without opening anything', () => {
    const { onSelect, onClose } = renderPalette();
    fireEvent.keyDown(input(), { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
    expect(onSelect).not.toHaveBeenCalled();
  });

  test('clicking the backdrop closes it', () => {
    const { onClose } = renderPalette();
    fireEvent.click(screen.getByRole('dialog'));
    expect(onClose).toHaveBeenCalled();
  });

  test('retyping resets the highlight to the best match', () => {
    // A highlight held across a query change points at an unrelated row,
    // and Enter would open the wrong task.
    const { onSelect } = renderPalette();
    fireEvent.keyDown(input(), { key: 'ArrowDown' });
    fireEvent.keyDown(input(), { key: 'ArrowDown' });
    fireEvent.change(input(), { target: { value: 'una' } });
    fireEvent.keyDown(input(), { key: 'Enter' });
    expect(onSelect).toHaveBeenCalledWith('UNA-2818');
  });

  test('pointing at a row makes it the Enter target too', () => {
    // The highlight IS the Enter target, so mouse and keyboard must not
    // disagree about what is selected.
    const { onSelect } = renderPalette();
    fireEvent.mouseMove(screen.getByText('ABC-7').closest('[role="option"]'));
    fireEvent.keyDown(input(), { key: 'Enter' });
    expect(onSelect).toHaveBeenCalledWith('ABC-7');
  });

  test('a query matching nothing says so instead of silently emptying', () => {
    renderPalette();
    fireEvent.change(input(), { target: { value: 'zzzzz' } });
    expect(screen.getByText(/no task matches/i)).toBeTruthy();
  });

  test('Enter on an empty result set does not open a task', () => {
    const { onSelect, onClose } = renderPalette();
    fireEvent.change(input(), { target: { value: 'zzzzz' } });
    fireEvent.keyDown(input(), { key: 'Enter' });
    expect(onSelect).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  test('the highlighted row is announced to screen readers', () => {
    renderPalette();
    expect(input().getAttribute('aria-activedescendant'))
      .toBe('task-palette-row-UNA-2818');
    fireEvent.keyDown(input(), { key: 'ArrowDown' });
    expect(input().getAttribute('aria-activedescendant'))
      .toBe('task-palette-row-UNA-1200');
  });

  test('it searches the operator rename shown on the tab', () => {
    const { onSelect } = renderPalette({
      nameFor: (session) => (session.task_id === 'ABC-7' ? 'billing overhaul' : ''),
    });
    fireEvent.change(input(), { target: { value: 'billing' } });
    fireEvent.keyDown(input(), { key: 'Enter' });
    expect(onSelect).toHaveBeenCalledWith('ABC-7');
  });
});

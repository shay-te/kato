// Bulk delete: pick several tasks, delete them, watch each one.
//
// Operator report: "I have 10-15 tasks open which I keep in case something
// breaks... when I delete tasks the delete is super slow and it hangs... show
// me some progress of the tasks that are being deleted, to queued to be
// deleted, and what the status of the delete is, just to know you are working
// and I am not deleting the tasks multiple times."

import { describe, test, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, cleanup, render, screen, fireEvent, within } from '@testing-library/react';

import BulkForgetModal from './BulkForgetModal.jsx';
import { DELETE_STATE, deleteQueue } from '../stores/deleteQueueStore.js';

function _sessions(...ids) {
  return ids.map((id) => ({ task_id: id, task_summary: `Summary ${id}` }));
}

function renderModal(extra = {}) {
  const onRun = vi.fn();
  const onClose = vi.fn();
  const utils = render(
    <BulkForgetModal
      sessions={_sessions('UNA-1', 'UNA-2', 'UNA-3')}
      onRun={onRun}
      onClose={onClose}
      {...extra}
    />,
  );
  return { ...utils, onRun, onClose };
}

describe('BulkForgetModal', () => {
  beforeEach(() => { deleteQueue.reset(); });
  afterEach(() => { cleanup(); deleteQueue.reset(); });

  test('nothing is selected until the operator picks', () => {
    renderModal();
    // Destructive action, so it must not arrive pre-armed.
    expect(screen.getByRole('button', { name: /Delete 0 tasks/ })).toBeDisabled();
  });

  test('selecting tasks arms the delete with a count', () => {
    renderModal();
    fireEvent.click(screen.getByLabelText('Select UNA-1'));
    fireEvent.click(screen.getByLabelText('Select UNA-3'));
    const button = screen.getByRole('button', { name: /Delete 2 tasks/ });
    expect(button).not.toBeDisabled();
  });

  test('select-all picks every row, and toggles back off', () => {
    renderModal();
    fireEvent.click(screen.getByLabelText('Select every task'));
    expect(screen.getByRole('button', { name: /Delete 3 tasks/ })).not.toBeDisabled();
    fireEvent.click(screen.getByLabelText('Select every task'));
    expect(screen.getByRole('button', { name: /Delete 0 tasks/ })).toBeDisabled();
  });

  test('confirming hands the selected ids to the runner', () => {
    const { onRun } = renderModal();
    fireEvent.click(screen.getByLabelText('Select UNA-2'));
    fireEvent.click(screen.getByRole('button', { name: /Delete 1 task/ }));
    expect(onRun).toHaveBeenCalledWith(['UNA-2']);
  });

  // ── the progress the operator asked for ────────────────────────────────
  test('each task shows queued, then deleting, then deleted', () => {
    renderModal();
    const row = () => within(screen.getByLabelText('Select UNA-1').closest('label'));

    act(() => { deleteQueue.enqueue(['UNA-1']); });
    expect(row().getByText('queued')).toBeInTheDocument();

    act(() => { deleteQueue.starting('UNA-1'); });
    expect(row().getByText(/deleting/)).toBeInTheDocument();

    act(() => { deleteQueue.succeeded('UNA-1'); });
    expect(row().getByText('deleted')).toBeInTheDocument();
  });

  test('a failed task says so and carries its reason', () => {
    renderModal();
    act(() => { deleteQueue.enqueue(['UNA-1']); });
    act(() => { deleteQueue.failed('UNA-1', 'workspace directory still exists'); });
    const cell = screen.getByText('failed').closest('.bulk-forget-state');
    expect(cell).toHaveAttribute('data-tooltip', 'workspace directory still exists');
  });

  test('while running, the controls lock so a second run cannot start', () => {
    // This is the anti-double-delete guarantee.
    renderModal();
    act(() => { deleteQueue.enqueue(['UNA-1', 'UNA-2']); });
    act(() => { deleteQueue.starting('UNA-1'); });

    expect(screen.getByRole('button', { name: /Deleting/ })).toBeDisabled();
    expect(screen.getByLabelText('Select UNA-2')).toBeDisabled();
    expect(screen.getByLabelText('Select every task')).toBeDisabled();
  });

  test('the footer reports progress while the run is in flight', () => {
    renderModal();
    act(() => { deleteQueue.enqueue(['UNA-1', 'UNA-2']); });
    act(() => { deleteQueue.starting('UNA-1'); });
    act(() => { deleteQueue.succeeded('UNA-1'); });
    expect(screen.getByRole('status')).toHaveTextContent('1 done');
  });

  test('Cancel is blocked mid-run so progress cannot be lost', () => {
    const { onClose } = renderModal();
    act(() => { deleteQueue.enqueue(['UNA-1']); });
    act(() => { deleteQueue.starting('UNA-1'); });
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
    expect(onClose).not.toHaveBeenCalled();
  });

  test('an empty board says so instead of showing a bare list', () => {
    renderModal({ sessions: [] });
    expect(screen.getByText('No tasks to delete.')).toBeInTheDocument();
    expect(screen.getByLabelText('Select every task')).toBeDisabled();
  });
});

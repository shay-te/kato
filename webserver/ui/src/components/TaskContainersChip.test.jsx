// The Docker containers a task's agent started, on the task's header.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchTaskContainers: vi.fn(),
  taskContainersAction: vi.fn(),
}));
vi.mock('../stores/toastStore.js', () => ({ toastResult: vi.fn() }));

import { fetchTaskContainers, taskContainersAction } from '../api.js';
import { toastResult } from '../stores/toastStore.js';
import TaskContainersChip from './TaskContainersChip.jsx';

const DB = { id: 'a1', name: 'una-1-db-1', image: 'postgres:16', state: 'running', running: true, project: 'una-1' };
const WEB = { id: 'b2', name: 'vite', image: 'node:22', state: 'exited', running: false, project: '' };

function serve(containers, available = true) {
  fetchTaskContainers.mockResolvedValue({ available, containers, reason: available ? '' : 'Docker is not running' });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('TaskContainersChip', () => {
  test('nothing at all without containers, or without Docker', async () => {
    serve([]);
    const { container, rerender } = render(<TaskContainersChip taskId="UNA-1" />);
    await waitFor(() => expect(fetchTaskContainers).toHaveBeenCalledWith('UNA-1'));
    expect(container).toBeEmptyDOMElement();
    serve([DB], false);
    rerender(<TaskContainersChip taskId="UNA-2" />);
    await waitFor(() => expect(fetchTaskContainers).toHaveBeenCalledWith('UNA-2'));
    expect(container).toBeEmptyDOMElement();
    fetchTaskContainers.mockRejectedValue(new Error('offline'));
    rerender(<TaskContainersChip taskId="UNA-3" />);
    await waitFor(() => expect(fetchTaskContainers).toHaveBeenCalledWith('UNA-3'));
    expect(container).toBeEmptyDOMElement();
  });

  test('the chip counts them and lists them', async () => {
    serve([DB, WEB]);
    render(<TaskContainersChip taskId="UNA-1" />);
    const chip = await screen.findByRole('button', { name: /1 running · 1 stopped/ });
    expect(chip).toHaveClass('is-running');
    fireEvent.click(chip);
    const menu = await screen.findByRole('menu', { name: 'Containers of UNA-1' });
    expect(within(menu).getByText('una-1-db-1')).toBeInTheDocument();
    expect(within(menu).getByText('node:22 · exited')).toBeInTheDocument();
  });

  test('Stop all stops them, says so, and reads them again', async () => {
    serve([DB]);
    taskContainersAction.mockResolvedValue({ ok: true, body: { containers: ['una-1-db-1'], problems: [] } });
    render(<TaskContainersChip taskId="UNA-1" />);
    fireEvent.click(await screen.findByRole('button', { name: /1 running/ }));
    serve([{ ...DB, state: 'exited', running: false }]);
    fireEvent.click(screen.getByRole('button', { name: 'Stop all' }));
    await waitFor(() => expect(taskContainersAction).toHaveBeenCalledWith('UNA-1', 'stop'));
    expect(await screen.findByRole('button', { name: /1 stopped/ })).toBeInTheDocument();
    expect(toastResult.mock.calls[0][0]).toMatchObject({ kind: 'success', title: 'Containers stopped', taskId: 'UNA-1' });
  });

  test('Remove all asks first; a refusal is a sticky warning', async () => {
    serve([DB, WEB]);
    taskContainersAction.mockResolvedValue({ ok: false, body: { containers: ['vite'], problems: ['una-1-db-1: busy'] } });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
    render(<TaskContainersChip taskId="UNA-1" />);
    fireEvent.click(await screen.findByRole('button', { name: /running/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Remove all' }));
    expect(taskContainersAction).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Remove all' }));
    await waitFor(() => expect(taskContainersAction).toHaveBeenCalledWith('UNA-1', 'remove'));
    await waitFor(() => expect(toastResult.mock.calls[0][0]).toMatchObject({
      kind: 'warning', message: '⚠ una-1-db-1: busy\n✓ vite',
    }));
    confirm.mockRestore();
  });

  test('a new agent status reads them again — a turn that started some shows them', async () => {
    serve([]);
    const { rerender } = render(<TaskContainersChip taskId="UNA-1" refreshKey="working" />);
    await waitFor(() => expect(fetchTaskContainers).toHaveBeenCalledTimes(1));
    serve([DB]);
    rerender(<TaskContainersChip taskId="UNA-1" refreshKey="idle" />);
    expect(await screen.findByRole('button', { name: /1 running/ })).toBeInTheDocument();
    await act(async () => {});
  });
});

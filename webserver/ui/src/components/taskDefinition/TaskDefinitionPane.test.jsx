// A task's own definition, read again: the report was "is there any option to
// view the original task description again?" — there was none.
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

vi.mock('../../api.js', () => ({ fetchTaskDefinition: vi.fn() }));

import { fetchTaskDefinition } from '../../api.js';
import TaskDefinitionPane from './TaskDefinitionPane.jsx';

beforeEach(() => { fetchTaskDefinition.mockReset(); });

describe('TaskDefinitionPane', () => {
  test('shows the title and the description as it was written', async () => {
    fetchTaskDefinition.mockResolvedValue({
      ok: true,
      body: { task_id: 'LOCAL-3', summary: 'Retry the export job', description: 'It fails on **timeouts**.' },
    });
    render(<TaskDefinitionPane taskId="LOCAL-3" />);
    expect(fetchTaskDefinition).toHaveBeenCalledWith('LOCAL-3');
    await waitFor(() => expect(screen.getByText('Retry the export job')).toBeInTheDocument());
    expect(screen.getByText('timeouts').tagName).toBe('STRONG');  // markdown, rendered
    expect(screen.getByRole('region', { name: 'Task LOCAL-3' })).toBeInTheDocument();
  });

  test('a task with only a title says so', async () => {
    fetchTaskDefinition.mockResolvedValue({ ok: true, body: { summary: 'Title only', description: '' } });
    render(<TaskDefinitionPane taskId="UNA-9" />);
    await waitFor(() => expect(screen.getByText(/no description — only its title/)).toBeInTheDocument());
  });

  test('a failed read says why, never an empty page', async () => {
    fetchTaskDefinition.mockResolvedValue({ ok: false, status: 404, body: { error: 'no workspace for task X-1' } });
    render(<TaskDefinitionPane taskId="X-1" />);
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Could not read the task: no workspace for task X-1'));
  });
});

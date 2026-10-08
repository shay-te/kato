// The "New task" pane against an in-memory kato: real api.js, real stores,
// real components — only ``fetch`` is answered here.
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

import NewTaskPane from './NewTaskPane.jsx';
import { _resetNewTaskDraft, openNewTaskDraft, readNewTaskDraft } from './newTaskDraftStore.js';

function fakeKato({ create = { status: 202, body: { task_id: 'LOCAL-1' } } } = {}) {
  const kato = { created: [] };
  vi.stubGlobal('fetch', async (url, init = {}) => {
    const respond = (status, body) => ({ ok: status < 400, status, statusText: '', json: async () => body });
    if (url === '/api/repositories') {
      return respond(200, { repositories: [{ id: 'web' }, { id: 'api' }, { id: 'billing' }] });
    }
    if (url === '/api/repository-approvals') {
      return respond(200, { repositories: [
        { repository_id: 'api', approved: true },
        { repository_id: 'web', approved: true },
        { repository_id: 'billing', approved: false },
      ] });
    }
    if (url === '/api/models') {
      return respond(200, { models: [
        { id: 'sonnet', label: 'Sonnet 5.5' }, { id: 'opus', label: 'Opus 5.5', default: true },
      ] });
    }
    if (url === '/api/effort-levels') {
      return respond(200, { levels: ['low', 'medium', 'high'], default: 'high' });
    }
    if (url === '/api/local-tasks') {
      kato.created.push(JSON.parse(init.body));
      return respond(create.status, create.body);
    }
    return respond(404, { error: `unknown route ${url}` });
  });
  return kato;
}

function renderPane() {
  const onCreated = vi.fn();
  const onHide = vi.fn();
  const view = render(<NewTaskPane onCreated={onCreated} onHide={onHide} />);
  return { onCreated, onHide, ...view };
}

async function fillIn() {
  fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Add retry' } });
  fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'Times out.' } });
  fireEvent.click(await screen.findByRole('checkbox', { name: /api/ }));
}

beforeEach(() => {
  _resetNewTaskDraft();
  openNewTaskDraft();
});

afterEach(() => {
  vi.unstubAllGlobals();
  _resetNewTaskDraft();
});

describe('NewTaskPane', () => {
  test('Plan is selected by default', async () => {
    fakeKato();
    renderPane();
    expect(screen.getByRole('radio', { name: /^Plan/ })).toBeChecked();
    expect(screen.getByRole('radio', { name: /Implement right away/ })).not.toBeChecked();
    expect(screen.getByRole('radio', { name: /Just open the chat/ })).not.toBeChecked();
  });

  test('Create waits for a title and a repository, and says so', async () => {
    fakeKato();
    renderPane();
    const create = screen.getByRole('button', { name: /Create/ });
    expect(create).toBeDisabled();
    expect(screen.getByText('Give the task a title · Pick at least one repository')).toBeInTheDocument();
    await fillIn();
    expect(create).toBeEnabled();
  });

  test('repositories are listed A→Z and an unapproved one cannot be picked', async () => {
    fakeKato();
    renderPane();
    const boxes = await screen.findAllByRole('checkbox');
    expect(boxes.map((box) => box.closest('label').textContent)).toEqual([
      'api', 'billingnot approved — Settings → Repositories', 'web',
    ]);
    expect(screen.getByRole('checkbox', { name: /billing/ })).toBeDisabled();
  });

  test('Create sends what the pickers show and opens the new task', async () => {
    const kato = fakeKato();
    const { onCreated } = renderPane();
    await fillIn();
    await screen.findByRole('option', { name: 'Opus 5.5' });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Create/ })); });
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith('LOCAL-1'));
    expect(kato.created).toEqual([{
      summary: 'Add retry', description: 'Times out.', repositories: ['api'],
      start_mode: 'plan', model: 'opus', effort: 'high',
    }]);
    expect(readNewTaskDraft().open).toBe(false);
    expect(readNewTaskDraft().title).toBe('');
  });

  test('another start mode and model are sent as picked', async () => {
    const kato = fakeKato();
    const { onCreated } = renderPane();
    await fillIn();
    fireEvent.click(screen.getByRole('radio', { name: /Implement right away/ }));
    await screen.findByRole('option', { name: 'Sonnet 5.5' });
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'sonnet' } });
    fireEvent.change(screen.getByLabelText('Effort'), { target: { value: 'low' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Create/ })); });
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(kato.created[0]).toMatchObject({ start_mode: 'implement', model: 'sonnet', effort: 'low' });
  });

  test('a refusal keeps the draft', async () => {
    fakeKato({ create: { status: 403, body: { error: 'not approved' } } });
    const { onCreated } = renderPane();
    await fillIn();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Create/ })); });
    expect(onCreated).not.toHaveBeenCalled();
    expect(readNewTaskDraft().title).toBe('Add retry');
    expect(readNewTaskDraft().open).toBe(true);
  });

  test('Preview renders the description as markdown', async () => {
    fakeKato();
    renderPane();
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: '# The spec' } });
    fireEvent.click(screen.getByRole('tab', { name: 'Preview' }));
    expect(screen.getByRole('heading', { name: 'The spec' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Write' }));
    expect(screen.getByLabelText('Description')).toHaveValue('# The spec');
  });

  test('what was typed survives closing and reopening the pane', async () => {
    fakeKato();
    const first = renderPane();
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Half written' } });
    first.unmount();
    renderPane();
    expect(screen.getByLabelText('Title')).toHaveValue('Half written');
  });

  test('the ✕ only hides the pane — the draft is kept', async () => {
    fakeKato();
    const { onHide } = renderPane();
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Keep me' } });
    fireEvent.click(screen.getByRole('button', { name: 'Hide the new task' }));
    expect(onHide).toHaveBeenCalled();
    expect(readNewTaskDraft().title).toBe('Keep me');
    expect(readNewTaskDraft().open).toBe(true);
  });
});

// The "New task" pane against an in-memory kato: real api.js, real stores,
// real components — only ``fetch`` is answered here.
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

import NewTaskPane from './NewTaskPane.jsx';
import { _resetNewTaskDraft, openNewTaskDraft, readNewTaskDraft } from './newTaskDraftStore.js';
import { readNewTaskAttachmentDraft } from '../../utils/newTaskAttachmentDraft.js';

// In-memory stand-in for IndexedDB (jsdom has none) — same shim used by
// MessageForm.test.jsx for the composer's own image draft. The attachments
// draft (newTaskAttachmentDraft.js) reads/writes through this.
const { _idbMem } = vi.hoisted(() => ({ _idbMem: new Map() }));
vi.mock('../../utils/idbStore.js', () => ({
  idbGet: async (key) => _idbMem.get(key),
  idbSet: async (key, value) => { _idbMem.set(key, value); },
  idbDelete: async (key) => { _idbMem.delete(key); },
  _resetIdbConnection: () => {},
}));

function textFile(name) {
  return new File(['x'], name, { type: 'text/plain' });
}

function fakeKato({ create = { status: 202, body: { task_id: 'LOCAL-1' } } } = {}) {
  const kato = { created: [] };
  vi.stubGlobal('fetch', async (url, init = {}) => {
    const respond = (status, body) => ({ ok: status < 400, status, statusText: '', json: async () => body });
    if (url === '/api/repositories') {
      return respond(200, { repositories: [{ id: 'web' }, { id: 'api' }, { id: 'billing' }] });
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
      // Plain JSON when there are no attachments (the common case) — a
      // FormData body (``payload`` + one ``attachments`` field per file)
      // only once the operator has picked files to send alongside the task.
      if (init.body instanceof FormData) {
        kato.created.push({
          ...JSON.parse(init.body.get('payload') || '{}'),
          attachments: init.body.getAll('attachments').map((f) => f.name),
        });
      } else {
        kato.created.push(JSON.parse(init.body));
      }
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
  _idbMem.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  _resetNewTaskDraft();
  _idbMem.clear();
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

  test('every repository kato found is listed A→Z, and any can be picked', async () => {
    fakeKato();
    renderPane();
    const boxes = await screen.findAllByRole('checkbox');
    expect(boxes.map((box) => box.closest('label').textContent)).toEqual(['api', 'billing', 'web']);
    expect(boxes.every((box) => !box.disabled)).toBe(true);
  });

  test('no repositories at all says why, and where to set the folder', async () => {
    fakeKato();
    const fetchImpl = globalThis.fetch;
    vi.stubGlobal('fetch', async (url, init) => (url === '/api/repositories'
      ? { ok: true, status: 200, statusText: '', json: async () => ({ repositories: [] }) }
      : fetchImpl(url, init)));
    const onOpenSettings = vi.fn();
    render(<NewTaskPane onCreated={vi.fn()} onHide={vi.fn()} onOpenSettings={onOpenSettings} />);
    expect(await screen.findByText(/No repositories found yet/)).toBeInTheDocument();
    expect(screen.queryByText('No repository matches.')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Open Settings → Repositories' }));
    expect(onOpenSettings).toHaveBeenCalledWith('repositories');
  });

  test('Create sends what the pickers show and opens the new task', async () => {
    const kato = fakeKato();
    const { onCreated } = renderPane();
    await fillIn();
    await screen.findByRole('option', { name: 'Opus 5.5' });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Create/ })); });
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith('LOCAL-1'));
    expect(kato.created).toEqual([{
      summary: 'Add retry', description: 'Times out.', repositories: ['api'], branch: '',
      start_mode: 'plan', model: 'opus', effort: 'high',
    }]);
    expect(readNewTaskDraft().open).toBe(false);
    expect(readNewTaskDraft().title).toBe('');
  });

  test('a branch name goes with the task and is optional', async () => {
    const kato = fakeKato();
    const { onCreated } = renderPane();
    await fillIn();
    expect(screen.getByRole('button', { name: /Create/ })).toBeEnabled();
    fireEvent.change(screen.getByLabelText('Branch name'), { target: { value: ' feature/retry ' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Create/ })); });
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(kato.created[0].branch).toBe('feature/retry');
  });

  test('a branch name the server refuses keeps the draft and shows why', async () => {
    const error = 'master is the branch api merges into, so the work would land on it with no pull request.';
    fakeKato({ create: { status: 400, body: { error } } });
    const { onCreated } = renderPane();
    await fillIn();
    fireEvent.change(screen.getByLabelText('Branch name'), { target: { value: 'master' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Create/ })); });
    await waitFor(() => expect(screen.getByLabelText('Branch name')).toHaveValue('master'));
    expect(onCreated).not.toHaveBeenCalled();
    expect(readNewTaskDraft().branch).toBe('master');
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
    fakeKato({ create: { status: 400, body: { error: 'no repository matches billing' } } });
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

  test('an attached file goes multipart, alongside the same fields', async () => {
    const kato = fakeKato();
    const { onCreated } = renderPane();
    await fillIn();
    fireEvent.change(screen.getByLabelText('Attach files'), { target: { files: [textFile('spec.pdf')] } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Create/ })); });
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith('LOCAL-1'));
    expect(kato.created).toEqual([{
      summary: 'Add retry', description: 'Times out.', repositories: ['api'], branch: '',
      start_mode: 'plan', model: 'opus', effort: 'high', attachments: ['spec.pdf'],
    }]);
  });

  test('no attachments stays plain JSON — the common case is unchanged', async () => {
    const kato = fakeKato();
    const { onCreated } = renderPane();
    await fillIn();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Create/ })); });
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(kato.created[0].attachments).toBeUndefined();
  });

  test('an attached file survives closing and reopening the pane, like the text draft', async () => {
    fakeKato();
    const first = renderPane();
    fireEvent.change(screen.getByLabelText('Attach files'), { target: { files: [textFile('spec.pdf')] } });
    await waitFor(async () => expect((await readNewTaskAttachmentDraft()).map((f) => f.name)).toEqual(['spec.pdf']));
    first.unmount();
    renderPane();
    expect(await screen.findByText('spec.pdf')).toBeInTheDocument();
  });

  test('a successful create clears the attachment draft too', async () => {
    const kato = fakeKato();
    const { onCreated } = renderPane();
    await fillIn();
    fireEvent.change(screen.getByLabelText('Attach files'), { target: { files: [textFile('spec.pdf')] } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Create/ })); });
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(kato.created[0].attachments).toEqual(['spec.pdf']);
    expect(await readNewTaskAttachmentDraft()).toEqual([]);
  });
});

describe('NewTaskPane — files pasted or dropped on the task itself', () => {
  function pngFile(name = 'image.png') {
    return new File(['png'], name, { type: 'image/png' });
  }
  function attachedNames() {
    return Array.from(document.querySelectorAll('.new-task-attachment-name')).map((el) => el.textContent);
  }

  test('a screenshot pasted into the description is attached', async () => {
    fakeKato();
    renderPane();
    const box = screen.getByLabelText('Description');
    fireEvent.paste(box, { clipboardData: { items: [
      { kind: 'file', type: 'image/png', getAsFile: () => pngFile() },
    ] } });
    await waitFor(() => expect(attachedNames()).toEqual(['image.png']));
  });

  test('pasting plain text still just types the text', () => {
    fakeKato();
    renderPane();
    const box = screen.getByLabelText('Description');
    const event = fireEvent.paste(box, { clipboardData: { items: [
      { kind: 'string', type: 'text/plain', getAsFile: () => null },
    ] } });
    expect(event).toBe(true); // not prevented: the browser inserts the text
    expect(attachedNames()).toEqual([]);
  });

  test('a file dropped on the description is attached, not opened over the draft', async () => {
    fakeKato();
    renderPane();
    const box = screen.getByLabelText('Description');
    const over = fireEvent.dragOver(box, { dataTransfer: { types: ['Files'] } });
    expect(over).toBe(false); // prevented: the browser would open the file
    fireEvent.drop(box, { dataTransfer: { types: ['Files'], files: [textFile('spec.pdf')] } });
    await waitFor(() => expect(attachedNames()).toEqual(['spec.pdf']));
  });

  test('a drop on the Attachments section is added once, not twice', async () => {
    fakeKato();
    renderPane();
    const section = screen.getByText('Attachments').closest('section');
    fireEvent.drop(section, { dataTransfer: { types: ['Files'], files: [textFile('a.csv')] } });
    await waitFor(() => expect(attachedNames()).toEqual(['a.csv']));
  });

  test('a pasted file goes out with the task, multipart', async () => {
    const kato = fakeKato();
    const { onCreated } = renderPane();
    await fillIn();
    fireEvent.paste(screen.getByLabelText('Description'), { clipboardData: { items: [
      { kind: 'file', type: 'image/png', getAsFile: () => pngFile('shot.png') },
    ] } });
    await waitFor(() => expect(attachedNames()).toEqual(['shot.png']));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Create/ })); });
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(kato.created[0].attachments).toEqual(['shot.png']);
  });
});

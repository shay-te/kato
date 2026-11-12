// Files that are neither an image the model can see nor plain text — a PDF, a
// spreadsheet, a zip, an oversized screenshot — used to be refused by the chat
// composer ("Only PNG… supported"), so a document could not be handed to the
// agent from the chat at all. They are now saved into the task folder (the
// same upload a too-large text file already used) and named in the message.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../utils/idbStore.js', () => ({
  idbGet: async () => undefined,
  idbSet: async () => {},
  idbDelete: async () => {},
  _resetIdbConnection: () => {},
}));

const uploadAttachment = vi.fn();
vi.mock('../api.js', () => ({
  fetchDraft: async () => ({ text: '', images: [] }),
  saveDraft: async () => {},
  uploadAttachment: (...args) => uploadAttachment(...args),
}));
vi.mock('../hooks/useAgentVersion.js', () => ({
  useAgentVersion: () => ({ supports_workflows: true }),
  resetAgentVersionCacheForTests: () => {},
}));
vi.mock('../stores/taskCache/index.js', () => ({
  useTaskTree: () => ({ trees: [], status: 'ready', error: null }),
}));
const toastShow = vi.fn();
vi.mock('../stores/toastStore.js', () => ({
  toast: { show: (...args) => toastShow(...args) },
}));

import MessageForm from './MessageForm.jsx';

function renderForm() {
  return render(<MessageForm taskId="LOCAL-3" turnInFlight={false} onSubmit={vi.fn()} />);
}

function file(name, type, size = 10) {
  const made = new File(['x'.repeat(size)], name, { type });
  return made;
}

function drop(files) {
  const form = document.querySelector('#message-form');
  fireEvent.drop(form, { dataTransfer: { types: ['Files'], files } });
}

beforeEach(() => {
  window.localStorage.clear();
  uploadAttachment.mockReset();
  toastShow.mockReset();
});

describe('MessageForm — documents', () => {
  test('a dropped PDF is saved in the task folder and named in the message', async () => {
    uploadAttachment.mockResolvedValue({
      ok: true, path: '/w/LOCAL-3/attachments/spec.pdf', bytes: 2048,
    });
    renderForm();

    drop([file('spec.pdf', 'application/pdf')]);

    await waitFor(() => expect(screen.getByRole('textbox').value).toContain('/w/LOCAL-3/attachments/spec.pdf'));
    expect(uploadAttachment).toHaveBeenCalledWith('LOCAL-3', expect.objectContaining({ name: 'spec.pdf' }));
    expect(screen.getByRole('textbox').value).toContain('`spec.pdf`');
    expect(toastShow).toHaveBeenCalledWith(expect.objectContaining({ kind: 'success' }));
  });

  test('a file pasted from the file manager goes the same way', async () => {
    uploadAttachment.mockResolvedValue({ ok: true, path: '/w/LOCAL-3/attachments/data.xlsx', bytes: 99 });
    renderForm();
    const xlsx = file('data.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');

    fireEvent.paste(screen.getByRole('textbox'), { clipboardData: { items: [
      { kind: 'file', type: xlsx.type, getAsFile: () => xlsx },
    ] } });

    await waitFor(() => expect(screen.getByRole('textbox').value).toContain('data.xlsx'));
    expect(uploadAttachment).toHaveBeenCalledTimes(1);
  });

  test('an image too big to ride in the message is saved instead of refused', async () => {
    uploadAttachment.mockResolvedValue({ ok: true, path: '/w/LOCAL-3/attachments/big.png', bytes: 6e6 });
    renderForm();

    drop([file('big.png', 'image/png', 6 * 1024 * 1024)]);

    await waitFor(() => expect(screen.getByRole('textbox').value).toContain('big.png'));
    expect(toastShow).not.toHaveBeenCalledWith(expect.objectContaining({ title: 'Image attachment rejected' }));
  });

  test('a small image still becomes a thumbnail, with no upload', async () => {
    renderForm();
    drop([file('shot.png', 'image/png')]);
    await waitFor(() => expect(document.querySelector('.composer-attachment, [class*="attachment"] img')).not.toBeNull());
    expect(uploadAttachment).not.toHaveBeenCalled();
  });

  test('a failed save says so and leaves the message alone', async () => {
    uploadAttachment.mockResolvedValue({ ok: false, error: 'file is larger than 64 MB' });
    renderForm();

    drop([file('huge.zip', 'application/zip')]);

    await waitFor(() => expect(toastShow).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'error', title: 'File attachment failed',
    })));
    expect(screen.getByRole('textbox').value).toBe('');
  });
});

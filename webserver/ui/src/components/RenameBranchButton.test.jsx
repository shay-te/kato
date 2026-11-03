import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({ renameTaskBranch: vi.fn() }));
vi.mock('../stores/toastStore.js', () => ({ toastResult: vi.fn() }));

import { renameTaskBranch } from '../api.js';
import { toastResult } from '../stores/toastStore.js';
import RenameBranchButton from './RenameBranchButton.jsx';

function open(props = {}) {
  const onRenamed = vi.fn();
  render(
    <RenameBranchButton taskId="T-1" branch="T-1" disabled={false} onRenamed={onRenamed} {...props} />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Rename branch' }));
  return { onRenamed, input: screen.getByLabelText('Branch name') };
}

const type = (input, value) => fireEvent.change(input, { target: { value } });
const confirm = () => fireEvent.click(screen.getByRole('button', { name: 'Rename' }));

describe('RenameBranchButton', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  test('opens on the name the branch has now', () => {
    expect(open().input.value).toBe('T-1');
  });

  test('cannot rename to the same name, or to nothing', () => {
    const { input } = open();
    expect(screen.getByRole('button', { name: 'Rename' }).disabled).toBe(true);
    type(input, '   ');
    expect(screen.getByRole('button', { name: 'Rename' }).disabled).toBe(true);
  });

  test('a rename reports, refreshes and closes', async () => {
    renameTaskBranch.mockResolvedValue({ ok: true, body: { branch: 'feature/a', renamed: ['api'] } });
    const { input, onRenamed } = open();
    type(input, '  feature/a ');
    confirm();
    await waitFor(() => expect(onRenamed).toHaveBeenCalled());
    expect(renameTaskBranch).toHaveBeenCalledWith('T-1', 'feature/a');
    expect(toastResult.mock.calls[0][0]).toMatchObject({ kind: 'success', title: 'Branch renamed' });
    expect(screen.queryByLabelText('Branch name')).toBeNull();
  });

  test('a refusal shows its reason in red and leaves the name to change', async () => {
    renameTaskBranch.mockResolvedValue({
      ok: false, status: 409, body: { error: 'a pull request is open from T-1 on api' },
    });
    const { input, onRenamed } = open();
    type(input, 'feature/a');
    confirm();
    await waitFor(() => expect(toastResult).toHaveBeenCalled());
    expect(toastResult.mock.calls[0][0]).toMatchObject({
      kind: 'error', title: 'Branch not renamed', message: 'a pull request is open from T-1 on api',
    });
    expect(onRenamed).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Branch name').value).toBe('feature/a');
  });

  test('Enter renames', async () => {
    renameTaskBranch.mockResolvedValue({ ok: true, body: { branch: 'feature/a', renamed: [] } });
    const { input } = open();
    type(input, 'feature/a');
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(renameTaskBranch).toHaveBeenCalled());
  });

  test('Enter on the same name does nothing', () => {
    const { input } = open();
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(renameTaskBranch).not.toHaveBeenCalled();
  });

  test('a pushed branch says it moved on the remote too', async () => {
    renameTaskBranch.mockResolvedValue({ ok: true, body: {
      branch: 'feature/a', renamed: ['api', 'web'], renamed_on_remote: ['api'], problems: [],
    } });
    const { input } = open();
    type(input, 'feature/a');
    confirm();
    await waitFor(() => expect(toastResult).toHaveBeenCalled());
    expect(toastResult.mock.calls[0][0]).toMatchObject({
      kind: 'success',
      message: 'Now feature/a in api, web.\nRenamed on the remote too: api.',
    });
  });

  test('a rename with problems is red and lists them first', async () => {
    // The rename stands, so the dialog still closes; the old branch left on
    // the remote is the operator's to clean up, and must not read as green.
    renameTaskBranch.mockResolvedValue({ ok: true, body: {
      branch: 'feature/a', renamed: ['api'], renamed_on_remote: ['api'],
      problems: ['T-1 is still on the remote of api: protected.'],
    } });
    const { input, onRenamed } = open();
    type(input, 'feature/a');
    confirm();
    await waitFor(() => expect(onRenamed).toHaveBeenCalled());
    const toast = toastResult.mock.calls[0][0];
    expect(toast).toMatchObject({ kind: 'error', title: 'Branch renamed, with problems' });
    expect(toast.message.split('\n')[0]).toBe('⚠ T-1 is still on the remote of api: protected.');
  });

  test('the dialog says the remote moves and unpushed commits stay', () => {
    open();
    expect(screen.getByText(/renamed on the remote too; commits you have not pushed stay here/)).toBeTruthy();
  });

  test('stays shut while git is busy', () => {
    render(<RenameBranchButton taskId="T-1" branch="T-1" disabled />);
    expect(screen.getByRole('button', { name: 'Rename branch' }).disabled).toBe(true);
  });
});

// Browse…: the computer's own folder dialog, the one folder button kato has.
// The API is the one stand-in, answering the way folder_dialog_routes.py does.
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  chooseFolderNatively: vi.fn(),
  fetchSettings: vi.fn(),
  updateSettings: vi.fn(),
}));
vi.mock('./settings/SchemaFieldGroup.jsx', () => ({ default: () => null }));

import * as api from '../api.js';
import { NO_FOLDER_DIALOG, RESTART_FOR_FOLDER_DIALOG, useNativeFolderDialog } from '../hooks/useNativeFolderDialog.js';
import BrowseFolderButton from './BrowseFolderButton.jsx';
import RepositoriesSettingsPanel from './RepositoriesSettingsPanel.jsx';

function Harness({ onPick }) {
  const dialog = useNativeFolderDialog();
  return (
    <>
      <BrowseFolderButton dialog={dialog} start="/home/me" prompt="Pick it" onPick={onPick} className="extra" />
      <output data-testid="error">{dialog.error}</output>
      <output data-testid="unavailable">{String(dialog.unavailable)}</output>
    </>
  );
}

const browse = () => fireEvent.click(screen.getByRole('button', { name: 'Browse…' }));

beforeEach(() => { vi.clearAllMocks(); });

describe('BrowseFolderButton', () => {
  test('the folder picked in the dialog is handed back', async () => {
    const onPick = vi.fn();
    api.chooseFolderNatively.mockResolvedValue({ ok: true, body: { path: '/home/me/dev' } });
    render(<Harness onPick={onPick} />);
    expect(screen.getByRole('button', { name: 'Browse…' })).toHaveClass('browse-folder-button', 'extra');
    browse();
    await waitFor(() => expect(onPick).toHaveBeenCalledWith('/home/me/dev'));
    expect(api.chooseFolderNatively).toHaveBeenCalledWith({ start: '/home/me', prompt: 'Pick it' });
    expect(screen.getByTestId('error')).toHaveTextContent('');
  });

  test('while it is open the button waits; a cancel hands back nothing', async () => {
    const onPick = vi.fn();
    let close;
    api.chooseFolderNatively.mockImplementation(() => new Promise((done) => { close = done; }));
    render(<Harness onPick={onPick} />);
    browse();
    expect(screen.getByRole('button', { name: 'Choose it in the folder dialog…' })).toBeDisabled();
    await act(async () => { close({ ok: true, body: { cancelled: true } }); });
    expect(screen.getByRole('button', { name: 'Browse…' })).toBeEnabled();
    expect(onPick).not.toHaveBeenCalled();
  });

  test('every way it can fail says why, and only "none here" marks it unavailable', async () => {
    const onPick = vi.fn();
    render(<Harness onPick={onPick} />);
    const cases = [
      [{ ok: false, status: 409, body: { error: 'a folder dialog is already open — finish or cancel it first' } },
        'a folder dialog is already open — finish or cancel it first', 'false'],
      [{ ok: false, status: 0, error: 'Failed to fetch' }, 'Failed to fetch', 'false'],
      [{ ok: false, status: 500, body: {} }, 'the folder dialog did not open', 'false'],
      [{ ok: false, status: 404, body: {} }, RESTART_FOR_FOLDER_DIALOG, 'false'],  // a kato older than the page
      [{ ok: false, status: 501, body: { error: 'no folder dialog can open on this computer' } }, NO_FOLDER_DIALOG, 'true'],
    ];
    for (const [answer, said, unavailable] of cases) {
      api.chooseFolderNatively.mockResolvedValueOnce(answer);
      browse();
      await waitFor(() => expect(screen.getByTestId('error')).toHaveTextContent(said));
      expect(screen.getByTestId('unavailable')).toHaveTextContent(unavailable);
    }
    expect(onPick).not.toHaveBeenCalled();
  });
});

describe('Settings → Repositories', () => {
  test('Browse… fills the folder path from the dialog, and says when none can open', async () => {
    api.fetchSettings.mockResolvedValue({ ok: true, body: {
      repository_root_path: { value: '/home/me/old', source: 'kato_settings' },
      settings_file_path: '/home/me/.kato/settings.json',
    } });
    render(<RepositoriesSettingsPanel />);
    await waitFor(() => expect(document.querySelector('.settings-drawer-input')?.value).toBe('/home/me/old'));
    const input = document.querySelector('.settings-drawer-input');

    api.chooseFolderNatively.mockResolvedValueOnce({ ok: true, body: { path: '/home/me/src' } });
    browse();
    await waitFor(() => expect(input.value).toBe('/home/me/src'));
    expect(api.chooseFolderNatively).toHaveBeenCalledWith({
      start: '/home/me/old', prompt: 'Choose the folder your repositories are in',
    });

    api.chooseFolderNatively.mockResolvedValueOnce({ ok: false, status: 501, body: {} });
    browse();
    expect(await screen.findByRole('alert')).toHaveTextContent(NO_FOLDER_DIALOG);
    expect(input.value).toBe('/home/me/src');  // the path is still typed by hand there
  });
});

// Tests for FolderBrowser — the inline directory picker behind "Browse…".
// Drives navigation (into a folder, Up, Home) against a stubbed
// /api/fs/dirs and pins the pick/cancel contract.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchDirectoryListing: vi.fn(),
}));

import { fetchDirectoryListing } from '../api.js';
import FolderBrowser from './FolderBrowser.jsx';

function listing(path, dirs, parent = '/') {
  return {
    path,
    parent,
    home: '/Users/dev',
    dirs: dirs.map((name) => ({ name, path: `${path}/${name}` })),
  };
}

beforeEach(() => {
  fetchDirectoryListing.mockReset();
});

describe('FolderBrowser', () => {
  test('lists folders and navigates into one on click', async () => {
    fetchDirectoryListing
      .mockResolvedValueOnce(listing('/Users/dev', ['Projects', 'Work']))
      .mockResolvedValueOnce(listing('/Users/dev/Projects', ['kato'], '/Users/dev'));
    render(<FolderBrowser initialPath="~" onPick={vi.fn()} onClose={vi.fn()} />);

    await screen.findByRole('button', { name: 'Projects' });
    fireEvent.click(screen.getByRole('button', { name: 'Projects' }));

    await screen.findByRole('button', { name: 'kato' });
    expect(fetchDirectoryListing).toHaveBeenLastCalledWith('/Users/dev/Projects');
    expect(screen.getByText('/Users/dev/Projects')).toBeInTheDocument();
  });

  test('Up navigates to the parent; disabled at the filesystem root', async () => {
    fetchDirectoryListing
      .mockResolvedValueOnce(listing('/Users/dev/Projects', [], '/Users/dev'))
      .mockResolvedValueOnce({ path: '/', parent: null, home: '/Users/dev', dirs: [] });
    render(<FolderBrowser initialPath="/Users/dev/Projects" onPick={vi.fn()} onClose={vi.fn()} />);

    await screen.findByText('(no subfolders)');
    fireEvent.click(screen.getByRole('button', { name: 'Up one folder' }));
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Up one folder' })).toBeDisabled();
    });
    expect(fetchDirectoryListing).toHaveBeenLastCalledWith('/Users/dev');
  });

  test('"Use this folder" picks the CURRENT directory', async () => {
    const onPick = vi.fn();
    fetchDirectoryListing.mockResolvedValue(listing('/Users/dev/Projects', ['kato'], '/Users/dev'));
    render(<FolderBrowser initialPath="/Users/dev/Projects" onPick={onPick} onClose={vi.fn()} />);

    await screen.findByRole('button', { name: 'kato' });
    fireEvent.click(screen.getByRole('button', { name: 'Use this folder' }));
    expect(onPick).toHaveBeenCalledWith('/Users/dev/Projects');
  });

  test('Cancel closes without picking', async () => {
    const onPick = vi.fn();
    const onClose = vi.fn();
    fetchDirectoryListing.mockResolvedValue(listing('/Users/dev', []));
    render(<FolderBrowser initialPath="~" onPick={onPick} onClose={onClose} />);

    await screen.findByText('(no subfolders)');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onClose).toHaveBeenCalled();
    expect(onPick).not.toHaveBeenCalled();
  });

  // Quick start offers ~/code before it exists: the picker opened there and
  // stopped at "could not list that folder", with Up and Home both dead.
  test('a folder that cannot be opened falls back to the home folder, and says so', async () => {
    fetchDirectoryListing.mockImplementation(async (path) => (path === '~'
      ? listing('/Users/dev', ['dev', 'Documents'])
      : Promise.reject(new Error('404'))));
    render(<FolderBrowser initialPath="/Users/dev/code" onPick={vi.fn()} onClose={vi.fn()} />);
    expect(await screen.findByText('/Users/dev/code could not be opened — showing your home folder.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /dev/ })).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  test('Home works even when nothing could be listed yet', async () => {
    fetchDirectoryListing.mockRejectedValueOnce(new Error('404')).mockRejectedValueOnce(new Error('down'))
      .mockResolvedValue(listing('/Users/dev', ['dev']));
    render(<FolderBrowser initialPath="/gone" onPick={vi.fn()} onClose={vi.fn()} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('could not open ~');
    fireEvent.click(screen.getByRole('button', { name: 'Go to home folder' }));
    expect(await screen.findByRole('button', { name: /dev/ })).toBeInTheDocument();
    expect(fetchDirectoryListing).toHaveBeenLastCalledWith('~');
  });

  test('the path reads left to right inside its right-to-left box', async () => {
    fetchDirectoryListing.mockResolvedValue(listing('/private/tmp/home', []));
    const { container } = render(<FolderBrowser initialPath="/private/tmp/home" onPick={vi.fn()} onClose={vi.fn()} />);
    const path = await screen.findByText('/private/tmp/home');
    expect(path.tagName).toBe('BDI');
    expect(path).toHaveAttribute('dir', 'ltr');
    expect(container.querySelector('.folder-browser-path')).toContainElement(path);
  });

  test('a listing error is shown without tearing the picker down', async () => {
    fetchDirectoryListing.mockResolvedValue({ error: 'permission denied: /root' });
    render(<FolderBrowser initialPath="/root" onPick={vi.fn()} onClose={vi.fn()} />);

    await screen.findByText('permission denied: /root');
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
  });
});

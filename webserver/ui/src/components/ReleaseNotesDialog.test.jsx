// The Release notes dialog: shows the file Settings point at, never the
// invisible task markers, and — when the Google Doc copy is on — how it stands
// and a "Sync now" that reports a failure loudly.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({
  fetchReleaseNotes: vi.fn(),
  syncReleaseNotes: vi.fn(),
}));

import { fetchReleaseNotes, syncReleaseNotes } from '../api.js';
import ReleaseNotesDialog from './ReleaseNotesDialog.jsx';

const NOTES = '# Release notes\n\n## Unreleased\n\n### backend\n\n'
  + '- 2026-10-21 — Clients can set a password (UNA-1) <!-- kato:task UNA-1 -->\n';

function answer(overrides = {}) {
  return {
    enabled: true, path: '/home/me/.kato/release-notes.md', exists: true, markdown: NOTES,
    google: { wanted: false, document_id: '', last_synced_at: 0, error: '' },
    ...overrides,
  };
}

async function open(data) {
  fetchReleaseNotes.mockResolvedValue(data);
  const onClose = vi.fn();
  render(<ReleaseNotesDialog onClose={onClose} />);
  await screen.findByText(data.path);
  return onClose;
}

beforeEach(() => {
  fetchReleaseNotes.mockReset();
  syncReleaseNotes.mockReset();
});

describe('ReleaseNotesDialog', () => {
  test('shows the notes and where the file is, without the task markers', async () => {
    await open(answer());
    expect(screen.getByRole('heading', { name: 'Unreleased' })).toBeInTheDocument();
    expect(screen.getByText('2026-10-21 — Clients can set a password (UNA-1)')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('kato:task');
    expect(screen.queryByRole('button', { name: 'Sync now' })).toBeNull();
  });

  test('an empty file says what will fill it', async () => {
    await open(answer({ exists: false, markdown: '' }));
    expect(screen.getByText(/When a task reaches Done, kato adds a dated line/)).toBeInTheDocument();
  });

  test('switched off says where to switch it on', async () => {
    await open(answer({ enabled: false, markdown: '' }));
    expect(screen.getByText(/switched off — Settings → Release notes/)).toBeInTheDocument();
  });

  test('Refresh reads it again', async () => {
    await open(answer());
    fetchReleaseNotes.mockResolvedValue(answer({ markdown: '# Release notes\n\n- newer line\n' }));
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    expect(await screen.findByText('newer line')).toBeInTheDocument();
    expect(fetchReleaseNotes).toHaveBeenCalledTimes(2);
  });

  test('a failed Google Doc copy is shown in red, and Sync now retries it', async () => {
    await open(answer({ google: { wanted: true, document_id: 'D', last_synced_at: 0, error: 'share the Doc with notes@' } }));
    expect(screen.getByRole('alert')).toHaveTextContent('Google Doc copy failed: share the Doc with notes@');

    syncReleaseNotes.mockResolvedValue({ ok: true, body: {} });
    fetchReleaseNotes.mockResolvedValue(answer({ google: { wanted: true, document_id: 'D', last_synced_at: Date.now() / 1000, error: '' } }));
    fireEvent.click(screen.getByRole('button', { name: 'Sync now' }));

    expect(await screen.findByText('Copied to the Google Doc just now.')).toBeInTheDocument();
    expect(syncReleaseNotes).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('alert')).toBeNull();
  });

  test('a Sync now the server refuses says why', async () => {
    await open(answer({ google: { wanted: true, document_id: 'D', last_synced_at: 0, error: '' } }));
    syncReleaseNotes.mockResolvedValue({ ok: false, body: { error: 'the Google Doc copy is off' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sync now' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('the Google Doc copy is off'));
  });

  test('a read that fails says so', async () => {
    fetchReleaseNotes.mockRejectedValue(new Error('503'));
    render(<ReleaseNotesDialog onClose={() => {}} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not read the release notes: 503');
  });

  test('closes on Escape', async () => {
    const onClose = await open(answer());
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });
});

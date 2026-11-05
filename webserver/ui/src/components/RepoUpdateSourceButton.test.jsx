// Update source for ONE repository: "I need to verify a small client change,
// but it pulls the backend too, which I don't want — Docker is building from it".
import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../api.js', () => ({ updateTaskSource: vi.fn() }));

import { updateTaskSource } from '../api.js';
import { gitActionKey, gitActionStore } from '../stores/gitActionStore.js';
import { toastStore } from '../stores/toastStore.js';
import RepoUpdateSourceButton from './RepoUpdateSourceButton.jsx';

beforeEach(() => {
  updateTaskSource.mockReset();
  gitActionStore.setBusy(gitActionKey('UNA-1', 'update-source'), false);
});

describe('RepoUpdateSourceButton', () => {
  test('updates only its repository and reports it, without folding the repo', async () => {
    updateTaskSource.mockResolvedValue({ ok: true, body: {
      updated: true, pushed: { pushed_repositories: ['ob-love-admin-client'] },
      updated_repositories: ['ob-love-admin-client'],
    } });
    const pushed = vi.spyOn(toastStore, 'push');
    const toggled = vi.fn();
    render(
      <header onClick={toggled}>
        <RepoUpdateSourceButton taskId="UNA-1" repoId="ob-love-admin-client" heading="ob-love-admin-client" />
      </header>,
    );
    const button = screen.getByRole('button', { name: 'Update the ob-love-admin-client source only' });
    expect(button).toHaveAttribute('data-tooltip', 'Update source — only ob-love-admin-client');
    fireEvent.click(button);

    expect(updateTaskSource).toHaveBeenCalledWith('UNA-1', 'ob-love-admin-client');
    expect(toggled).not.toHaveBeenCalled();
    await waitFor(() => expect(pushed).toHaveBeenCalledWith(expect.objectContaining({
      title: 'Source updated',
      message: expect.stringContaining('source updated for 1 repo(s): ob-love-admin-client'),
      durationMs: 0,  // sticky, like the header's
    })));
  });

  test('shares the task\'s one git slot: busy while the header\'s Update source runs', () => {
    gitActionStore.setBusy(gitActionKey('UNA-1', 'update-source'), true);
    render(<RepoUpdateSourceButton taskId="UNA-1" repoId="api" heading="api" />);
    const button = screen.getByRole('button', { name: 'Updating the api source…' });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(updateTaskSource).not.toHaveBeenCalled();
  });
});

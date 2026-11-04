// Settings → Approvals: a repository approved now is trusted unless the
// operator picks restricted. Restricted also needs the Docker sandbox and a
// stricter scanner, which a new install has neither of, so as the default it
// refused every task kato picked up by itself.

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const api = vi.hoisted(() => ({
  fetchRepositoryApprovals: vi.fn(),
  updateRepositoryApprovals: vi.fn(),
}));
vi.mock('../api.js', () => api);
vi.mock('../stores/toastStore.js', () => ({ toast: { show: vi.fn(), errorFromResult: vi.fn() } }));
vi.mock('./settings/SchemaFieldGroup.jsx', () => ({ default: () => null }));

const { default: RepositoryApprovalsSettingsPanel } = await import('./RepositoryApprovalsSettingsPanel.jsx');

function rows(...list) {
  return { ok: true, body: { repositories: list, storage_path: '/x/approved.json' } };
}

beforeEach(() => {
  api.fetchRepositoryApprovals.mockReset();
  api.updateRepositoryApprovals.mockReset();
  api.updateRepositoryApprovals.mockResolvedValue({ ok: true, body: { applied: { approved: [], revoked: [] } } });
});

describe('RepositoryApprovalsSettingsPanel', () => {
  test('a repository approved now is trusted unless restricted is picked', async () => {
    api.fetchRepositoryApprovals.mockResolvedValue(rows(
      { repository_id: 'api', approved: false, approval_mode: '', remote_url: 'git@x:api.git', source: 'checkout' },
    ));
    render(<RepositoryApprovalsSettingsPanel />);
    const toggle = await screen.findByRole('checkbox');
    expect(screen.getByRole('combobox')).toHaveValue('trusted');
    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(api.updateRepositoryApprovals).toHaveBeenCalled());
    expect(api.updateRepositoryApprovals.mock.calls[0][0].approve).toEqual([
      { repository_id: 'api', remote_url: 'git@x:api.git', mode: 'trusted' },
    ]);
  });

  test('an approval saved without a mode is shown as what it is: restricted', async () => {
    api.fetchRepositoryApprovals.mockResolvedValue(rows(
      { repository_id: 'old', approved: true, approval_mode: '', remote_url: 'u', source: 'checkout' },
      { repository_id: 'vendor', approved: true, approval_mode: 'restricted', remote_url: 'v', source: 'checkout' },
    ));
    render(<RepositoryApprovalsSettingsPanel />);
    const [old, vendor] = await screen.findAllByRole('combobox');
    expect(old).toHaveValue('restricted');
    expect(vendor).toHaveValue('restricted');
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled();  // nothing changed
  });

  test('the panel says what restricted needs', async () => {
    api.fetchRepositoryApprovals.mockResolvedValue(rows());
    render(<RepositoryApprovalsSettingsPanel />);
    expect(await screen.findByText(/the Docker sandbox on/)).toBeInTheDocument();
    expect(screen.getByText(/MEDIUM findings/)).toBeInTheDocument();
    expect(screen.getByText(/Set the folder that holds your repositories/)).toBeInTheDocument();
  });
});

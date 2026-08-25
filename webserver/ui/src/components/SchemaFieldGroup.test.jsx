/**
 * Repo-scope settings live on the Approvals tab, not in General.
 *
 * ``KATO_IGNORED_REPOSITORY_FOLDERS`` / ``KATO_REPOSITORY_DENYLIST`` decide
 * which repos kato may touch at all, so the schema tags them
 * ``panel: 'approvals'``. Two invariants matter and are pinned here:
 *   1. the tagged field renders on the Approvals tab, and
 *   2. it renders EXACTLY ONCE — the generic section panel must skip it, or
 *      two independent drafts fight over the same key.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, cleanup } from '@testing-library/react';

const SECTIONS = [{
  id: 'general',
  label: 'General',
  title: 'General',
  description: 'General settings.',
  fields: [
    { key: 'KATO_MAX_PARALLEL_TASKS', type: 'number', label: 'Max parallel tasks', help: '', value: '2' },
    {
      key: 'KATO_IGNORED_REPOSITORY_FOLDERS', type: 'text',
      label: 'Ignored repo folders', help: 'Comma-separated.',
      value: 'node_modules', panel: 'approvals',
    },
    {
      key: 'KATO_REPOSITORY_DENYLIST', type: 'text',
      label: 'Repository denylist', help: 'Never touch.',
      value: '', panel: 'approvals',
    },
  ],
}];

vi.mock('../api.js', () => ({
  fetchAllSettings: vi.fn(() => Promise.resolve({
    ok: true, body: { sections: SECTIONS, settings_file_path: '~/.kato/settings.json' },
  })),
  updateAllSettings: vi.fn(() => Promise.resolve({ ok: true, body: {} })),
  fetchOpenRouterModels: vi.fn(() => Promise.resolve([])),
  fetchRepositoryApprovals: vi.fn(() => Promise.resolve({
    ok: true, body: { repositories: [], storage_path: '~/.kato/approvals.json' },
  })),
  updateRepositoryApprovals: vi.fn(() => Promise.resolve({ ok: true, body: {} })),
}));

const SchemaFieldGroup = (await import('./settings/SchemaFieldGroup.jsx')).default;
const SchemaSettingsPanel = (await import('./SchemaSettingsPanel.jsx')).default;
const RepositoryApprovalsSettingsPanel =
  (await import('./RepositoryApprovalsSettingsPanel.jsx')).default;
const { buildSettingsIndex, filterSettingsIndex } =
  await import('../utils/settingsSearch.js');

describe('SchemaFieldGroup', () => {
  beforeEach(() => { vi.clearAllMocks(); });
  afterEach(() => { cleanup(); });

  it('renders only the fields tagged for its panel', async () => {
    render(<SchemaFieldGroup sectionId="general" panel="approvals" />);
    await screen.findByText('Ignored repo folders');
    expect(screen.getByText('Repository denylist')).toBeTruthy();
    expect(screen.queryByText('Max parallel tasks')).toBeNull();
  });

  it('shows its title and description', async () => {
    render(
      <SchemaFieldGroup
        sectionId="general" panel="approvals"
        title="Repository scope" description="Applied before the table."
      />,
    );
    await screen.findByText('Repository scope');
    expect(screen.getByText('Applied before the table.')).toBeTruthy();
  });

  it('renders nothing when no field carries the tag', async () => {
    const { container } = render(
      <SchemaFieldGroup sectionId="general" panel="nobody" />,
    );
    await waitFor(() => {
      expect(container.querySelector('.settings-drawer-field-group')).toBeNull();
    });
  });

  it('seeds each field from its saved value', async () => {
    render(<SchemaFieldGroup sectionId="general" panel="approvals" />);
    await screen.findByText('Ignored repo folders');
    const input = document.querySelector(
      '[data-field-key="KATO_IGNORED_REPOSITORY_FOLDERS"] input',
    );
    expect(input.value).toBe('node_modules');
  });
});

describe('the generic section panel skips lifted fields', () => {
  afterEach(() => { cleanup(); });

  it('does not render a panel-tagged field in its own section tab', async () => {
    render(<SchemaSettingsPanel sectionId="general" />);
    await screen.findByText('Max parallel tasks');
    expect(screen.queryByText('Ignored repo folders')).toBeNull();
    expect(screen.queryByText('Repository denylist')).toBeNull();
  });
});

describe('the Approvals tab hosts the repo-scope fields', () => {
  afterEach(() => { cleanup(); });

  it('renders them under the approvals table', async () => {
    render(<RepositoryApprovalsSettingsPanel />);
    await screen.findByText('Repository scope');
    expect(screen.getByText('Ignored repo folders')).toBeTruthy();
    expect(screen.getByText('Repository denylist')).toBeTruthy();
  });
});

describe('settings search follows the field to its bespoke tab', () => {
  const BESPOKE = [
    { id: 'repositories', label: 'Repositories' },
    { id: 'approvals', label: 'Approvals' },
  ];

  it('routes a panel-tagged field to the tab that renders it', () => {
    const index = buildSettingsIndex(SECTIONS, BESPOKE);
    const [hit] = filterSettingsIndex(index, 'KATO_IGNORED_REPOSITORY_FOLDERS');
    expect(hit.tabId).toBe('approvals');
    expect(hit.section).toBe('Approvals');
  });

  it('leaves untagged fields on their schema tab', () => {
    const index = buildSettingsIndex(SECTIONS, BESPOKE);
    const [hit] = filterSettingsIndex(index, 'KATO_MAX_PARALLEL_TASKS');
    expect(hit.tabId).toBe('schema:general');
    expect(hit.section).toBe('General');
  });

  it('falls back to the schema tab when no bespoke tab matches', () => {
    const [hit] = filterSettingsIndex(
      buildSettingsIndex(SECTIONS, []), 'KATO_REPOSITORY_DENYLIST',
    );
    expect(hit.tabId).toBe('schema:general');
  });
});

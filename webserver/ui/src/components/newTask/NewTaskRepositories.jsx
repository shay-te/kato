import { useState } from 'react';
import { cx } from '../../utils/cx.js';
import { usePickerData } from '../../hooks/usePickerData.js';
import { fetchInventoryRepositories, fetchRepositoryApprovals } from '../../api.js';
import { approvedIdSet, repositoryChoices, toggleRepository } from './newTaskHelpers.js';
import {
  APPROVALS_LOCATION,
  SETTINGS_TAB_APPROVALS,
  SETTINGS_TAB_REPOSITORIES,
} from '../../constants/settingsTabs.js';

// Every repository kato knows, with the approvals beside them: a repository
// REP has not approved can't be picked (the agent is not allowed in it).
async function loadRepositories() {
  const [inventory, approvals] = await Promise.all([
    fetchInventoryRepositories(),
    fetchRepositoryApprovals(),
  ]);
  if (!inventory.ok) { throw new Error(inventory.error || 'could not list the repositories'); }
  return {
    repositories: inventory.body?.repositories || [],
    approved: approvals.ok ? approvedIdSet(approvals.body) : null,
  };
}

function statusText({ loading, error, count, filter, total }) {
  if (loading) { return 'Loading repositories…'; }
  if (error) { return error; }
  // None at all is not a filter problem: kato has not found any yet.
  if (total === 0) { return 'No repositories found yet. Set the folder that holds them in Settings → Repositories.'; }
  if (count === 0 && filter) { return 'No repository matches.'; }
  return '';
}

export default function NewTaskRepositories({ selected, onChange, onOpenSettings = null }) {
  const [filter, setFilter] = useState('');
  const { data, loading, error } = usePickerData(loadRepositories, [], null);
  const choices = data ? repositoryChoices(data.repositories, data.approved, { filter, selected }) : [];
  const toggle = (repositoryId) => { onChange(toggleRepository(selected, repositoryId)); };
  const openApprovals = onOpenSettings ? () => onOpenSettings(SETTINGS_TAB_APPROVALS) : null;
  const rows = choices.map((choice) => (
    <RepositoryRow key={choice.id} choice={choice} onToggle={toggle} onOpenApprovals={openApprovals} />
  ));
  const total = data ? data.repositories.length : -1;
  const status = statusText({ loading, error, count: choices.length, filter, total });
  const openRepositories = onOpenSettings && total === 0 ? (
    <button type="button" className="new-task-repo-link" onClick={() => onOpenSettings(SETTINGS_TAB_REPOSITORIES)}>
      Open Settings → Repositories
    </button>
  ) : null;
  const statusLine = status ? <p className="new-task-repos-status">{status} {openRepositories}</p> : null;
  const countLabel = `${selected.length} selected`;
  return (
    <section className="new-task-section new-task-repos">
      <h4 className="new-task-section-title">
        Repositories
        <span className="new-task-repos-count">{countLabel}</span>
      </h4>
      <input
        type="search"
        className="new-task-repos-filter"
        placeholder="Filter…"
        aria-label="Filter repositories"
        value={filter}
        onChange={(event) => setFilter(event.target.value)}
      />
      {statusLine}
      <ul className="new-task-repos-list">{rows}</ul>
    </section>
  );
}

function RepositoryRow({ choice, onToggle, onOpenApprovals }) {
  const blocked = !choice.approved;
  // An already-picked repository stays un-tickable even if it lost approval.
  const disabled = blocked && !choice.selected;
  // Where to approve it, as a way there — not a name to go and find.
  const where = onOpenApprovals ? (
    <button type="button" className="new-task-repo-link" onClick={onOpenApprovals}>{APPROVALS_LOCATION}</button>
  ) : APPROVALS_LOCATION;
  const note = blocked ? (
    <span className="new-task-repo-note">not approved — approve it in {where}</span>
  ) : null;
  return (
    <li className={cx('new-task-repo', blocked && 'is-unapproved')}>
      <label>
        <input
          type="checkbox"
          checked={choice.selected}
          disabled={disabled}
          onChange={() => onToggle(choice.id)}
        />
        <span className="new-task-repo-name">{choice.id}</span>
        {note}
      </label>
    </li>
  );
}

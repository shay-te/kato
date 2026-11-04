import { useState } from 'react';
import { usePickerData } from '../../hooks/usePickerData.js';
import { fetchInventoryRepositories } from '../../api.js';
import { repositoryChoices, toggleRepository } from './newTaskHelpers.js';
import { SETTINGS_TAB_REPOSITORIES } from '../../constants/settingsTabs.js';

// Every repository kato knows — every one under the repository folder,
// except the folders Settings says to ignore.
async function loadRepositories() {
  const inventory = await fetchInventoryRepositories();
  if (!inventory.ok) { throw new Error(inventory.error || 'could not list the repositories'); }
  return { repositories: inventory.body?.repositories || [] };
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
  const choices = data ? repositoryChoices(data.repositories, { filter, selected }) : [];
  const toggle = (repositoryId) => { onChange(toggleRepository(selected, repositoryId)); };
  const rows = choices.map((choice) => <RepositoryRow key={choice.id} choice={choice} onToggle={toggle} />);
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

function RepositoryRow({ choice, onToggle }) {
  return (
    <li className="new-task-repo">
      <label>
        <input type="checkbox" checked={choice.selected} onChange={() => onToggle(choice.id)} />
        <span className="new-task-repo-name">{choice.id}</span>
      </label>
    </li>
  );
}

import { useMemo, useState } from 'react';
import { fetchInventoryRepositories } from '../api.js';
import { apiErrorMessage } from '../utils/apiError.js';
import { usePickerData } from '../hooks/usePickerData.js';
import SearchPickerModal from './SearchPickerModal.jsx';

// Pick one repository from kato's inventory — every repo kato knows under the
// repository root (the dev folder). Searchable by id, owner or slug; the ones
// already chosen are left out. What picking does is the caller's
// (``onPick(repo)``): the Files tab adds it to the task, the plan split adds
// it to every task of the split.
export default function RepositoryPickerModal({
  ariaLabel,
  title,
  helpText,
  confirmLabel,
  busyLabel,
  alreadyAttachedIds = new Set(),
  allAttachedText,
  onPick,
  onClose,
}) {
  const [query, setQuery] = useState('');
  const { data: repositories, loading, error } = usePickerData(async () => {
    const result = await fetchInventoryRepositories();
    if (!result.ok) { throw new Error(apiErrorMessage(result, 'failed to load repositories')); }
    return Array.isArray(result.body?.repositories) ? result.body.repositories : [];
  }, [], []);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const attached = new Set(
      Array.from(alreadyAttachedIds || []).map((id) => String(id).toLowerCase()),
    );
    return (repositories || []).filter((repo) => {
      const id = String(repo.id || '').toLowerCase();
      if (attached.has(id)) { return false; }
      if (!needle) { return true; }
      const haystack = `${id}\n${repo.owner || ''}\n${repo.repo_slug || ''}`
        .toLowerCase();
      return haystack.includes(needle);
    });
  }, [repositories, query, alreadyAttachedIds]);

  return (
    <SearchPickerModal
      ariaLabel={ariaLabel}
      title={title}
      onClose={onClose}
      helpText={helpText}
      searchPlaceholder="Search by id, owner, or slug…"
      query={query}
      onQueryChange={setQuery}
      items={filtered}
      loading={loading}
      error={error}
      loadingText="Loading repositories…"
      emptyText={repositories.length === 0 ? 'No repositories configured in kato.' : allAttachedText}
      getItemId={(repo) => repo.id}
      confirmLabel={confirmLabel}
      busyLabel={busyLabel}
      onConfirm={onPick}
      renderRow={(repo) => (
        <>
          <div className="adopt-session-row-top">
            <span className="adopt-session-cwd">{repo.id}</span>
            <span className="adopt-session-meta">
              {repo.owner && repo.repo_slug
                ? `${repo.owner}/${repo.repo_slug}`
                : (repo.owner || repo.repo_slug || '')}
            </span>
          </div>
          {repo.local_path && (
            <div className="adopt-session-preview">{repo.local_path}</div>
          )}
        </>
      )}
    />
  );
}

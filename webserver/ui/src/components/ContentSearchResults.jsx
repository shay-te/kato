import { useEffect, useState } from 'react';
import { searchTaskWorkspaceContent } from '../api.js';
import { groupContentMatchesByFile } from '../FilesTabHelpers.js';

// Content (grep) search results for the Files tab. Shown when the search
// box has a query: fetches matching LINES across the task's repos and
// lists them grouped by file. Clicking a line opens that file in the
// editor (via onOpenFile). Complements the filename Cmd+P tree filter —
// this is how you find a symbol like ``project_list`` by its content.
export default function ContentSearchResults({
  taskId, query, onOpenFile, scopeRepoId = '',
}) {
  const [state, setState] = useState({ status: 'idle', matches: [], truncated: false });

  useEffect(() => {
    const q = String(query || '').trim();
    if (!taskId || q.length < 2) {
      setState({ status: 'idle', matches: [], truncated: false });
      return undefined;
    }
    let cancelled = false;
    setState((prev) => ({ ...prev, status: 'loading' }));
    searchTaskWorkspaceContent(taskId, q)
      .then((body) => {
        if (cancelled) { return; }
        setState({
          status: 'ready',
          matches: Array.isArray(body?.matches) ? body.matches : [],
          truncated: !!body?.truncated,
        });
      })
      .catch(() => {
        if (!cancelled) { setState({ status: 'error', matches: [], truncated: false }); }
      });
    return () => { cancelled = true; };
  }, [taskId, query]);

  if (state.status === 'idle') { return null; }

  // Scoped to the same repo the file list is. Searching a multi-repo task
  // put the operator's repo behind a wall of another repo's hits; a scope
  // that applied to only half the results would be worse than none.
  const scoped = scopeRepoId
    ? state.matches.filter((m) => (m && m.repo_id) === scopeRepoId)
    : state.matches;
  const groups = groupContentMatchesByFile(scoped);
  let inner;
  if (state.status === 'loading') {
    inner = <p className="files-tab-message">Searching contents…</p>;
  } else if (state.status === 'error') {
    inner = <p className="files-tab-message error">Content search failed.</p>;
  } else if (groups.length === 0) {
    inner = <p className="files-tab-message">No content matches.</p>;
  } else {
    inner = groups.map((group) => (
      <ContentFileGroup key={group.key} group={group} onOpenFile={onOpenFile} />
    ));
  }

  return (
    <div className="files-content-search">
      <div className="files-content-search-head">
        Content matches
        {state.truncated ? ' (showing first 200)' : ''}
      </div>
      {inner}
    </div>
  );
}


function ContentFileGroup({ group, onOpenFile }) {
  function openAt(line) {
    if (typeof onOpenFile !== 'function') { return; }
    onOpenFile({
      absolutePath: group.absPath,
      relativePath: group.path,
      repoId: group.repoId,
      line,
    });
  }
  return (
    <div className="files-content-search-file">
      <div className="files-content-search-path" title={`${group.repoId}/${group.path}`}>
        <span className="files-content-search-repo">{group.repoId}</span>
        {' / '}
        {group.path}
      </div>
      {group.lines.map((m) => (
        <button
          type="button"
          key={`${m.line}:${m.text}`}
          className="files-content-search-line"
          onClick={() => openAt(m.line)}
        >
          <span className="files-content-search-lineno">{m.line}</span>
          <span className="files-content-search-snippet">{m.text.trim()}</span>
        </button>
      ))}
    </div>
  );
}

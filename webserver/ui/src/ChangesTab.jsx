import { useEffect, useState } from 'react';
import { parseDiff, Diff, Hunk } from 'react-diff-view';
import 'react-diff-view/style/index.css';
import { fetchDiff } from './api.js';

export default function ChangesTab({ taskId, workspaceVersion = 0 }) {
  const [state, setState] = useState({
    status: 'loading',
    files: [],
    base: '',
    head: '',
    error: '',
  });

  useEffect(() => {
    if (!taskId) { return; }
    let cancelled = false;
    setState({ status: 'loading', files: [], base: '', head: '', error: '' });
    fetchDiff(taskId)
      .then((payload) => {
        if (cancelled) { return; }
        const raw = String(payload.diff || '');
        setState({
          status: 'ready',
          files: raw ? parseDiff(raw) : [],
          base: payload.base || '',
          head: payload.head || '',
          error: '',
        });
      })
      .catch((err) => {
        if (cancelled) { return; }
        setState({
          status: 'error',
          files: [],
          base: '',
          head: '',
          error: String(err),
        });
      });
    return () => { cancelled = true; };
  }, [taskId, workspaceVersion]);

  return (
    <div className="changes-tab">
      <header className="changes-tab-header">
        {state.base && state.head ? (
          <span>
            <code>{state.base}</code> … <code>{state.head}</code>
          </span>
        ) : (
          <span>diff</span>
        )}
      </header>
      <div className="changes-tab-body">
        {state.status === 'loading' && (
          <p className="changes-tab-message">Computing diff…</p>
        )}
        {state.status === 'error' && (
          <p className="changes-tab-message error">{state.error}</p>
        )}
        {state.status === 'ready' && state.files.length === 0 && (
          <p className="changes-tab-message">
            No changes between <code>{state.base}</code> and{' '}
            <code>{state.head}</code>.
          </p>
        )}
        {state.status === 'ready' && state.files.map((file) => (
          <DiffFile key={diffFileKey(file)} file={file} />
        ))}
      </div>
    </div>
  );
}

function diffFileKey(file) {
  const oldPath = file.oldPath || '';
  const newPath = file.newPath || '';
  return `${file.type}:${oldPath}->${newPath}`;
}

function DiffFile({ file }) {
  const path = file.newPath || file.oldPath || '(unknown)';
  return (
    <section className="diff-file">
      <header className="diff-file-header">
        <span className="diff-file-type">{file.type}</span>
        <span className="diff-file-path">{path}</span>
      </header>
      <Diff viewType="unified" diffType={file.type} hunks={file.hunks || []}>
        {(hunks) => hunks.map((hunk) => (
          <Hunk key={hunk.content} hunk={hunk} />
        ))}
      </Diff>
    </section>
  );
}

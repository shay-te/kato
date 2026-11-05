import { useState } from 'react';
import { fetchTaskDefinition } from '../../api.js';
import { useSettingsResource } from '../../hooks/useSettingsResource.js';
import MarkdownContent from '../MarkdownContent.jsx';

// A task's own definition — the title and description it was created with (a
// ticket's, or what was typed in New task) — as a tab among its files, so it
// can be read again. It is what the agent was given as its task (the same
// workspace record). Read when the tab opens; never polled.
export default function TaskDefinitionPane({ taskId }) {
  const [definition, setDefinition] = useState(null);
  const { loading, error } = useSettingsResource(
    () => fetchTaskDefinition(taskId), setDefinition,
  );
  let body;
  if (loading) {
    body = <p className="task-definition-note">Loading the task…</p>;
  } else if (error) {
    body = <p className="task-definition-note is-error" role="alert">{`Could not read the task: ${error}`}</p>;
  } else {
    const description = String(definition?.description || '').trim();
    body = description
      ? <div className="task-definition-markdown"><MarkdownContent>{description}</MarkdownContent></div>
      : <p className="task-definition-note">This task has no description — only its title.</p>;
  }
  return (
    <section className="task-definition-pane" aria-label={`Task ${taskId}`}>
      <header className="task-definition-header">
        <strong className="task-definition-id">{taskId}</strong>
        <span className="task-definition-title">{definition?.summary || ''}</span>
      </header>
      <div className="task-definition-body">{body}</div>
    </section>
  );
}

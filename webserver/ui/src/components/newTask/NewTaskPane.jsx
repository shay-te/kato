import { useState } from 'react';
import { cx } from '../../utils/cx.js';
import { createLocalTask, fetchEffortLevels, fetchModels } from '../../api.js';
import { useBusyAction } from '../../hooks/useBusyAction.js';
import { usePickerData } from '../../hooks/usePickerData.js';
import { loadCatalog } from '../../stores/catalogStore.js';
import { toast, toastResult } from '../../stores/toastStore.js';
import Icon, { BusyIcon } from '../Icon.jsx';
import MarkdownContent from '../MarkdownContent.jsx';
import NewTaskAgentPickers from './NewTaskAgentPickers.jsx';
import NewTaskRepositories from './NewTaskRepositories.jsx';
import NewTaskStartMode from './NewTaskStartMode.jsx';
import { discardNewTaskDraft, updateNewTaskDraft, useNewTaskDraft } from './newTaskDraftStore.js';
import { draftPayload, draftProblems, effectiveEffort, effectiveModel } from './newTaskHelpers.js';

// The same cached catalogues the chat composer's pickers read.
async function loadAgentChoices() {
  const [models, effort] = await Promise.all([
    loadCatalog('models', fetchModels),
    loadCatalog('levels', fetchEffortLevels),
  ]);
  return { models: models?.models || [], effort: effort || { levels: [], default: '' } };
}

// "New task": a task written here, in kato — no tracker. The description
// takes the whole height (it can be a full spec); the settings sit in a narrow
// column beside it. Everything typed is kept as a draft in this browser.
// ``onHide`` only returns the centre to the file view; the draft stays in its
// tab. Discarding it is the tab's close, which asks first.
export default function NewTaskPane({ onCreated, onHide }) {
  const draft = useNewTaskDraft();
  const [previewing, setPreviewing] = useState(false);
  const choices = usePickerData(loadAgentChoices, [], null).data;
  const models = choices?.models || [];
  const levels = choices?.effort?.levels || [];
  const model = effectiveModel(draft.model, models);
  const effort = effectiveEffort(draft.effort, choices?.effort);
  const problems = draftProblems(draft);
  const [creating, create] = useBusyAction(async () => {
    const result = await createLocalTask(draftPayload(draft, { model, effort }));
    if (!result.ok) {
      toast.errorFromResult(result, { title: 'The task was not created' });
      return;
    }
    const taskId = result.body.task_id;
    toastResult({
      kind: 'success',
      title: `${taskId} created`,
      message: 'Cloning its repositories — the chat starts when they are ready.',
      taskId,
    });
    discardNewTaskDraft();
    onCreated(taskId);
  }, { enabled: problems.length === 0 });

  const editor = previewing ? (
    <div className="new-task-preview"><MarkdownContent>{draft.description || '_Nothing written yet._'}</MarkdownContent></div>
  ) : (
    <textarea
      className="new-task-description"
      aria-label="Description"
      placeholder="What should be done? Paste the spec, acceptance criteria, links… (markdown)"
      value={draft.description}
      onChange={(event) => updateNewTaskDraft({ description: event.target.value })}
    />
  );
  const createIcon = creating ? <BusyIcon /> : <Icon name="play" />;
  const blockedHint = problems.length ? <p className="new-task-problems">{problems.join(' · ')}</p> : null;
  return (
    <section id="new-task-pane" aria-label="New task">
      <header className="new-task-header">
        <Icon name="edit" />
        <h3 className="new-task-heading">New task</h3>
        <button
          type="button"
          className="new-task-close"
          aria-label="Hide the new task"
          title="Back to the file view — the draft stays in its tab"
          onClick={onHide}
        >
          <Icon name="xmark" />
        </button>
      </header>
      <div className="new-task-body">
        <div className="new-task-main">
          <input
            className="new-task-title"
            aria-label="Title"
            placeholder="Title"
            value={draft.title}
            onChange={(event) => updateNewTaskDraft({ title: event.target.value })}
          />
          <div className="new-task-editor-tabs" role="tablist">
            <button type="button" role="tab" aria-selected={!previewing}
              className={cx('new-task-editor-tab', !previewing && 'active')}
              onClick={() => setPreviewing(false)}>Write</button>
            <button type="button" role="tab" aria-selected={previewing}
              className={cx('new-task-editor-tab', previewing && 'active')}
              onClick={() => setPreviewing(true)}>Preview</button>
          </div>
          {editor}
        </div>
        <aside className="new-task-side">
          <NewTaskRepositories
            selected={draft.repositories}
            onChange={(repositories) => updateNewTaskDraft({ repositories })}
          />
          <NewTaskStartMode
            value={draft.startMode}
            onChange={(startMode) => updateNewTaskDraft({ startMode })}
          />
          <NewTaskAgentPickers
            models={models}
            levels={levels}
            model={model}
            effort={effort}
            onChange={updateNewTaskDraft}
          />
          {blockedHint}
          <button
            type="button"
            className="new-task-create"
            disabled={creating || problems.length > 0}
            onClick={create}
          >
            {createIcon}
            Create
          </button>
        </aside>
      </div>
    </section>
  );
}

import { useEffect, useState } from 'react';
import { promptStore, EDITABLE_PROMPTS } from '../stores/promptStore.js';
import { toast } from '../stores/toastStore.js';
import SettingsPanelBody from './settings/SettingsPanelBody.jsx';
import SettingsPanelHead from './settings/SettingsPanelHead.jsx';

// "Prompts" tab — edit the predefined chat prompts (today: the Code
// review button's prompt). Edits persist to localStorage via promptStore
// and the button picks them up immediately; the shipped .md default is
// always one "Reset to default" click away.

export default function PromptsSettingsPanel() {
  return (
    <div className="settings-drawer-panel">
      <SettingsPanelHead title="Prompts">
        <p>
          Customize the predefined prompts kato sends. Your text is saved
          in this browser and used the next time you trigger the prompt.
          <strong> Reset to default</strong> restores the shipped version.
        </p>
      </SettingsPanelHead>
      <SettingsPanelBody>
        <>
          {EDITABLE_PROMPTS.map((meta) => (
            <PromptEditor key={meta.id} meta={meta} />
          ))}
        </>
      </SettingsPanelBody>
    </div>
  );
}


function PromptEditor({ meta }) {
  const [draft, setDraft] = useState(() => promptStore.get(meta.id));
  const [custom, setCustom] = useState(() => promptStore.isCustom(meta.id));

  // Re-sync if the override changes elsewhere (another tab / a reset).
  useEffect(() => promptStore.subscribe(() => {
    setDraft(promptStore.get(meta.id));
    setCustom(promptStore.isCustom(meta.id));
  }), [meta.id]);

  const dirty = draft !== promptStore.get(meta.id);

  function save() {
    promptStore.setOverride(meta.id, draft);
    toast.show({
      kind: 'success', title: 'Prompt saved',
      message: `“${meta.label}” will use your custom text.`, durationMs: 4000,
    });
  }

  function resetToDefault() {
    promptStore.reset(meta.id);
    setDraft(meta.default);
    toast.show({
      kind: 'info', title: 'Prompt reset',
      message: `“${meta.label}” restored to the shipped default.`, durationMs: 4000,
    });
  }

  return (
    <div className="settings-prompt-editor">
      <div className="settings-prompt-editor-head">
        <span className="settings-drawer-field-label">{meta.label}</span>
        <span className={`settings-drawer-source source-${custom ? 'custom' : 'default'}`}>
          {custom ? 'custom' : 'default'}
        </span>
      </div>
      <p className="settings-drawer-field-hint">{meta.description}</p>
      <textarea
        className="settings-drawer-input settings-prompt-textarea"
        value={draft}
        spellCheck={false}
        onChange={(ev) => setDraft(ev.target.value)}
        rows={16}
        aria-label={`${meta.label} prompt`}
      />
      <div className="settings-drawer-actions">
        <button
          type="button"
          className="settings-drawer-action-secondary"
          onClick={resetToDefault}
          disabled={!custom}
        >
          Reset to default
        </button>
        <button
          type="button"
          className="settings-drawer-action-primary"
          onClick={save}
          disabled={!dirty}
        >
          Save
        </button>
      </div>
    </div>
  );
}

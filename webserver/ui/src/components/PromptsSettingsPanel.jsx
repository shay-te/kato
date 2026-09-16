import { useEffect, useState } from 'react';
import {
  FAST_PROMPT_ICONS, promptStore, useFastPrompts,
} from '../stores/promptStore.js';
import { toast } from '../stores/toastStore.js';
import Icon from './Icon.jsx';
import SettingsPanelBody from './settings/SettingsPanelBody.jsx';
import SettingsPanelHead from './settings/SettingsPanelHead.jsx';

// "Prompts" tab — the fast prompts on the session toolbar, one button each.
// Code review ships with kato; any prompt's name, icon and text can be
// changed, and the operator can add their own. Each prompt is collapsed to a
// single row until opened, so the list stays scannable as it grows. Saved in
// this browser through promptStore; the toolbar picks every change up at once.

const NEW_PROMPT = Object.freeze({ label: '', icon: 'send', text: '' });

export default function PromptsSettingsPanel() {
  const prompts = useFastPrompts();
  const [openIds, setOpenIds] = useState(() => new Set());
  const [adding, setAdding] = useState(false);

  function toggle(id) {
    setOpenIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) { next.delete(id); } else { next.add(id); }
      return next;
    });
  }

  return (
    <div className="settings-drawer-panel">
      <SettingsPanelHead title="Prompts">
        <p>
          Fast prompts sit on the session toolbar — one click sends the prompt
          to the agent. Change a prompt&apos;s name, icon or text, or add your
          own. Saved in this browser; <strong>Reset to default</strong> restores
          a prompt kato ships.
        </p>
      </SettingsPanelHead>
      <SettingsPanelBody>
        <>
          {prompts.map((prompt) => (
            <PromptEditor
              key={prompt.id}
              prompt={prompt}
              open={openIds.has(prompt.id)}
              onToggle={() => toggle(prompt.id)}
            />
          ))}
          {adding ? (
            <PromptEditor prompt={null} open onClose={() => setAdding(false)} />
          ) : (
            <div className="settings-drawer-actions">
              <button
                type="button"
                className="settings-drawer-action-secondary"
                onClick={() => setAdding(true)}
              >
                Add prompt
              </button>
            </div>
          )}
        </>
      </SettingsPanelBody>
    </div>
  );
}


function PromptEditor({ prompt, open, onToggle = null, onClose = null }) {
  const isNew = !prompt;
  const saved = prompt || NEW_PROMPT;
  const [draft, setDraft] = useState(() => fieldsOf(saved));

  // Re-sync when the saved prompt changes (a save, a reset, another editor).
  useEffect(() => {
    if (prompt) { setDraft(fieldsOf(prompt)); }
  }, [prompt]);

  // Accessible names follow the SAVED name, so they stay put while typing.
  const name = isNew ? 'New' : saved.label;
  const valid = !!draft.label.trim() && !!draft.text.trim();
  const dirty = draft.label !== saved.label
    || draft.icon !== saved.icon
    || draft.text !== saved.text;
  const update = (field, value) => setDraft((prev) => ({ ...prev, [field]: value }));
  // An icon is one click, so it applies on that click for a prompt that
  // already exists; a new prompt's icon rides along with its Add.
  function chooseIcon(icon) {
    update('icon', icon);
    if (!isNew) { promptStore.setIcon(prompt.id, icon); }
  }

  function save() {
    if (isNew) {
      promptStore.add(draft);
      toast.show({
        kind: 'success', title: 'Prompt added',
        message: `“${draft.label.trim()}” is on the session toolbar.`, durationMs: 4000,
      });
      onClose();
      return;
    }
    promptStore.save(prompt.id, draft);
    toast.show({
      kind: 'success', title: 'Prompt saved',
      message: `“${draft.label.trim()}” will use your changes.`, durationMs: 4000,
    });
  }

  function resetToDefault() {
    promptStore.reset(prompt.id);
    toast.show({
      kind: 'info', title: 'Prompt reset',
      message: `“${prompt.label}” restored to the shipped default.`, durationMs: 4000,
    });
  }

  function remove() {
    promptStore.remove(prompt.id);
    toast.show({
      kind: 'info', title: 'Prompt deleted',
      message: `“${prompt.label}” is off the session toolbar.`, durationMs: 4000,
    });
  }

  const headContent = (
    <>
      {onToggle && <Icon name={open ? 'chevron-down' : 'chevron-right'} />}
      <Icon name={draft.icon} />
      <span className="settings-drawer-field-label">
        {isNew ? draft.label.trim() || 'New prompt' : saved.label}
      </span>
      {prompt && (
        <span className={`settings-drawer-source source-${badgeOf(prompt) === 'default' ? 'default' : 'custom'}`}>
          {badgeOf(prompt)}
        </span>
      )}
    </>
  );

  return (
    <div className="settings-prompt-editor">
      {onToggle ? (
        <button
          type="button"
          className="settings-prompt-editor-head"
          aria-expanded={open}
          onClick={onToggle}
        >
          {headContent}
        </button>
      ) : (
        <div className="settings-prompt-editor-head">{headContent}</div>
      )}
      {open && (
        <>
          {/* Both fields are LABELLED. The icon row read as decoration next to
              a bare text box, so "let me choose its icon" was asked for
              something that was already there but did not look like a
              control. */}
          <span className="settings-drawer-field-label">Name</span>
          <input
            type="text"
            className="settings-drawer-input is-compact"
            value={draft.label}
            placeholder="Name shown on the toolbar"
            onChange={(ev) => update('label', ev.target.value)}
            aria-label={`${name} name`}
          />
          {/* Named as a CHOICE, with the current pick spelled out. A bare row
              of glyphs next to a labelled text box reads as decoration —
              asked twice for an icon chooser that was already there. */}
          <span className="settings-drawer-field-label">
            {`Icon — pick one (now: ${draft.icon})`}
            {isNew ? ' — saved with the prompt' : ' — applied as you pick it'}
          </span>
          <div
            className="settings-prompt-icon-picker"
            role="radiogroup"
            aria-label={`${name} icon`}
          >
            {FAST_PROMPT_ICONS.map((icon) => (
              <button
                key={icon}
                type="button"
                role="radio"
                aria-checked={draft.icon === icon}
                aria-label={icon}
                className={`settings-prompt-icon-option${draft.icon === icon ? ' is-active' : ''}`}
                title={icon}
                onClick={() => chooseIcon(icon)}
              >
                <Icon name={icon} />
                {draft.icon === icon && (
                  <span className="settings-prompt-icon-check" aria-hidden="true">✓</span>
                )}
              </button>
            ))}
          </div>
          <textarea
            className="settings-drawer-input settings-prompt-textarea"
            value={draft.text}
            spellCheck={false}
            placeholder="The prompt sent to the agent"
            onChange={(ev) => update('text', ev.target.value)}
            rows={12}
            aria-label={`${name} prompt`}
          />
          <div className="settings-drawer-actions">
            {isNew && (
              <button type="button" className="settings-drawer-action-secondary" onClick={onClose}>
                Cancel
              </button>
            )}
            {prompt?.builtin && (
              <button
                type="button"
                className="settings-drawer-action-secondary"
                onClick={resetToDefault}
                disabled={!prompt.changed}
              >
                Reset to default
              </button>
            )}
            {prompt && !prompt.builtin && (
              <button type="button" className="settings-drawer-action-secondary" onClick={remove}>
                Delete
              </button>
            )}
            <button
              type="button"
              className="settings-drawer-action-primary"
              onClick={save}
              disabled={!valid || (!isNew && !dirty)}
            >
              {isNew ? 'Add' : 'Save'}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function fieldsOf(prompt) {
  return { label: prompt.label, icon: prompt.icon, text: prompt.text };
}

// "default" / "custom" for a prompt kato ships, "added" for the operator's own.
function badgeOf(prompt) {
  if (!prompt.builtin) { return 'added'; }
  return prompt.changed ? 'custom' : 'default';
}

import { useMemo, useState } from 'react';
import { fetchAllSettings, updateAllSettings } from '../api.js';
import { useRestartingSave } from './useRestartingSave.js';
import { useSettingsResource } from './useSettingsResource.js';
import { countNoun } from '../utils/pluralize.js';

// Shared load → seed → diff → save → revert state for ONE section of the
// ``/api/all-settings`` schema. Both the generic SchemaSettingsPanel and
// bespoke panels (Action Guard) build on this so the boilerplate lives once.
//
// Returns the section + its fields, the editable ``draft`` with ``setField``,
// the ``dirtyKeys`` diff, ``revert``, the ``save`` state machine (which POSTs
// only the changed keys), and ``restartRequired`` — the server's verdict on
// the last save, so a panel only shows the restart banner when it's true.
// ``successMessage`` overrides the save toast for sections that apply live
// (Action Guard) instead of needing a restart.
export function useSchemaSectionDraft(sectionId, { onSaved, successMessage } = {}) {
  const [meta, setMeta] = useState({ sections: [], settingsFilePath: '' });
  const [draft, setDraft] = useState({});

  function seedFrom(fields) {
    const seed = {};
    for (const f of fields) { seed[f.key] = f.value ?? ''; }
    setDraft(seed);
  }

  const { loading, error, refresh } = useSettingsResource(fetchAllSettings, (body) => {
    const sections = Array.isArray(body.sections) ? body.sections : [];
    setMeta({ sections, settingsFilePath: String(body.settings_file_path || '') });
    const section = sections.find((s) => s.id === sectionId);
    seedFrom(section?.fields || []);
  });

  const section = useMemo(
    () => meta.sections.find((s) => s.id === sectionId) || null,
    [meta.sections, sectionId],
  );

  const dirtyKeys = useMemo(() => {
    const out = [];
    for (const f of (section?.fields || [])) {
      if (String(draft[f.key] ?? '') !== String(f.value ?? '')) { out.push(f.key); }
    }
    return out;
  }, [section, draft]);

  // Whether the LAST save actually needs a restart. The server answers this
  // per-save (a section can hold both restart-only keys and live ones, e.g.
  // the review-comment switch in General), so trusting a per-panel constant
  // would show "restart kato" after a change that already took effect.
  const [restartRequired, setRestartRequired] = useState(true);

  const saveOpts = {
    onSaved: (result) => {
      setRestartRequired(result?.body?.restart_required !== false);
      refresh();
      if (onSaved) { onSaved(result); }
    },
  };
  if (successMessage) { saveOpts.successMessage = successMessage; }
  const { saving, savedAt, save } = useRestartingSave(() => {
    const updates = {};
    for (const k of dirtyKeys) { updates[k] = draft[k]; }
    return updateAllSettings(updates);
  }, saveOpts);

  function setField(key, value) {
    setDraft((cur) => ({ ...cur, [key]: value }));
  }
  function revert() {
    seedFrom(section?.fields || []);
  }

  // Ready-to-spread props for the shared <SettingsActions> save bar, so the
  // identical Save/Revert wiring isn't duplicated in every panel.
  const saveBarProps = {
    onSecondary: revert,
    secondaryDisabled: saving || dirtyKeys.length === 0,
    onSave: save,
    saving,
    canSave: dirtyKeys.length > 0,
    primaryLabel: dirtyKeys.length
      ? `Save ${countNoun(dirtyKeys.length, 'change')}`
      : 'Save',
  };

  return {
    loading, error, refresh,
    section, fields: section?.fields || [],
    settingsFilePath: meta.settingsFilePath,
    draft, setField, dirtyKeys, revert,
    saving, savedAt, save, saveBarProps, restartRequired,
  };
}

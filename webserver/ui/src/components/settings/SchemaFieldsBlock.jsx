import { SchemaField } from '../SchemaSettingsPanel.jsx';
import SettingsActions from './SettingsActions.jsx';
import RestartBanner from './RestartBanner.jsx';

// The rows-plus-save-bar half of a schema settings panel: the field widgets,
// the Save/Revert bar, and the "restart kato" banner the last save asked for.
//
// Shared by the generic per-section panel (SchemaSettingsPanel) and the
// lifted-field group a bespoke tab embeds (SchemaFieldGroup) — the two differ
// only in WHICH fields they pass in, so the rendering lives once.
export default function SchemaFieldsBlock({
  fields, draft, setField, fieldsRef, saveBarProps, savedAt, restartRequired,
  showActions = true,
}) {
  return (
    <>
      <div className="settings-drawer-fields" ref={fieldsRef}>
        {(fields || []).map((f) => (
          <SchemaField
            key={f.key}
            field={f}
            value={draft[f.key] ?? ''}
            onChange={(v) => setField(f.key, v)}
          />
        ))}
      </div>

      {showActions && <SettingsActions {...saveBarProps} />}

      <RestartBanner show={savedAt && restartRequired} />
    </>
  );
}

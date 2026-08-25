import { useRef } from 'react';
import { useSchemaSectionDraft } from '../../hooks/useSchemaSectionDraft.js';
import { useFieldHighlight } from '../../hooks/useFieldHighlight.js';
import PanelMessage from './PanelMessage.jsx';
import SchemaFieldsBlock from './SchemaFieldsBlock.jsx';

// Renders the schema fields a section tagged with ``panel: <name>`` inside a
// BESPOKE tab instead of that section's generic one. The schema stays the
// single source of truth for the field's type/label/help — only its
// placement moves. ``SchemaSettingsPanel`` skips the same fields, so a
// tagged field renders exactly once across the whole drawer.
//
// Saving posts only this group's dirty keys (``useSchemaSectionDraft``
// diffs against the loaded values), so it can never clobber the rest of
// the section it was lifted out of.

export default function SchemaFieldGroup({
  sectionId, panel, title, description, highlightKey = '',
}) {
  const fieldsRef = useRef(null);
  const {
    loading, error, section, draft, setField, savedAt, saveBarProps,
    restartRequired, dirtyKeys,
  } = useSchemaSectionDraft(sectionId);

  const fields = (section?.fields || []).filter((f) => f.panel === panel);
  useFieldHighlight(fieldsRef, highlightKey, [section]);

  if (loading) {
    return <PanelMessage>Loading settings…</PanelMessage>;
  }
  if (error) {
    return <PanelMessage error>{error}</PanelMessage>;
  }
  if (!fields.length) {
    return null;
  }

  return (
    <div className="settings-drawer-field-group">
      {title && <h4 className="settings-drawer-field-group-title">{title}</h4>}
      {description && (
        <p className="settings-drawer-field-group-help">{description}</p>
      )}

      <SchemaFieldsBlock
        fields={fields}
        draft={draft}
        setField={setField}
        fieldsRef={fieldsRef}
        saveBarProps={saveBarProps}
        savedAt={savedAt}
        restartRequired={restartRequired}
        // The save bar only appears once there is something to save: this
        // group sits at the BOTTOM of a tab that already has its own, and a
        // second permanently-disabled Save reads as the tab's own button
        // having broken.
        showActions={dirtyKeys.length > 0}
      />
    </div>
  );
}

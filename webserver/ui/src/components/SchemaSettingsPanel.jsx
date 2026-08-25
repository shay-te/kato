import { useEffect, useRef, useState } from 'react';
import { fetchOpenRouterModels } from '../api.js';
import { useSchemaSectionDraft } from '../hooks/useSchemaSectionDraft.js';
import { useFieldHighlight } from '../hooks/useFieldHighlight.js';
import { sourceLabel } from '../utils/settingsSource.js';
import { fieldPlaceholder } from '../utils/fieldHelp.js';
import FieldInfoTip from './settings/FieldInfoTip.jsx';
import PanelMessage from './settings/PanelMessage.jsx';
import SchemaFieldsBlock from './settings/SchemaFieldsBlock.jsx';
import SettingsPanelHead from './settings/SettingsPanelHead.jsx';

// Generic, schema-driven settings panel. One instance renders ONE
// section of the ``/api/all-settings`` schema (General, Claude
// agent, Sandbox, Security scanner, Email & Slack, OpenHands,
// Docker/infra, AWS). Field widgets are chosen from ``field.type``;
// ``warning`` / ``danger`` annotations render inline. The section's
// own ``warning`` renders as a banner (the Sandbox tab uses this).
//
// Writes go to ~/.kato/settings.json via POST /api/all-settings
// (server whitelists to the schema) — kato's only config file.
// Most keys are read at boot, so a save shows the restart banner; the
// server flags the ones that apply live (the review-comment switch) and
// the banner stays hidden for those.

export default function SchemaSettingsPanel({ sectionId, highlightKey = '' }) {
  const fieldsRef = useRef(null);
  const {
    loading, error, section, settingsFilePath,
    draft, setField, savedAt, saveBarProps, restartRequired,
  } = useSchemaSectionDraft(sectionId);

  // When the operator jumps here from the settings search, scroll the matched
  // field into view and flash it so they spot the one they searched for.
  useFieldHighlight(fieldsRef, highlightKey, [sectionId, section]);

  if (loading) {
    return (
      <div className="settings-drawer-panel">
        <PanelMessage>Loading settings…</PanelMessage>
      </div>
    );
  }
  if (error) {
    return (
      <div className="settings-drawer-panel">
        <PanelMessage error>{error}</PanelMessage>
      </div>
    );
  }
  if (!section) {
    return (
      <div className="settings-drawer-panel">
        <PanelMessage>Unknown settings section.</PanelMessage>
      </div>
    );
  }

  return (
    <div className="settings-drawer-panel">
      <SettingsPanelHead title={section.title || section.label}>
        <p>
          {section.description}
          {' '}Saved to
          {' '}<code>{settingsFilePath || '~/.kato/settings.json'}</code>
          {' '}— kato&apos;s only config file.
        </p>
      </SettingsPanelHead>

      {section.warning && (
        <div className="settings-drawer-section-warning">
          ⚠ {section.warning}
        </div>
      )}

      {/* A field tagged with ``panel`` lives in a bespoke tab (see
          SchemaFieldGroup) — skipping it here keeps it from rendering
          twice, with two independent drafts fighting over one key. */}
      <SchemaFieldsBlock
        fields={section.fields.filter((f) => !f.panel)}
        draft={draft}
        setField={setField}
        fieldsRef={fieldsRef}
        saveBarProps={saveBarProps}
        savedAt={savedAt}
        restartRequired={restartRequired}
      />
    </div>
  );
}


// Lazy, process-wide loaders for a field's ``datalist`` autocomplete source, so a
// large live catalogue (OpenRouter ships 300+ models) is fetched at most once and
// shared across every field/render that asks for it.
// Wrapped in arrows so the api.js export is only touched when a datalist field is
// actually rendered — tests that mock api.js without this export (and never open
// the OpenRouter field) don't trip vitest's missing-export guard at module load.
const DATALIST_LOADERS = { openrouter: () => fetchOpenRouterModels() };
const _datalistCache = {};
const _datalistPromises = {};

// Test-only: drop the memoised datalist results so a test can re-stub the loader.
export function resetDatalistCacheForTests() {
  for (const key of Object.keys(_datalistCache)) delete _datalistCache[key];
  for (const key of Object.keys(_datalistPromises)) delete _datalistPromises[key];
}

function loadDatalist(name) {
  if (_datalistCache[name]) {
    return Promise.resolve(_datalistCache[name]);
  }
  if (!_datalistPromises[name]) {
    const loader = DATALIST_LOADERS[name];
    _datalistPromises[name] = (loader ? loader() : Promise.resolve([]))
      .then((opts) => {
        _datalistCache[name] = Array.isArray(opts) ? opts : [];
        return _datalistCache[name];
      })
      .catch(() => (_datalistCache[name] = []));
  }
  return _datalistPromises[name];
}

// Exported for unit tests (datalist autocomplete + widget selection). Not a
// public component — render the panel, not this, in app code.
export function SchemaField({ field, value, onChange }) {
  const isBool = field.type === 'bool';
  const isSelect = field.type === 'select';
  const isSecret = field.type === 'secret';
  const isNumber = field.type === 'number';
  const boolChecked = String(value).toLowerCase() === 'true';

  // Live autocomplete source (e.g. OpenRouter's catalogue) for free-text fields
  // that opt in via ``field.datalist`` — keeps the input free text, just suggested.
  const [datalistOptions, setDatalistOptions] = useState(
    () => (field.datalist ? _datalistCache[field.datalist] || [] : []),
  );
  useEffect(() => {
    if (!field.datalist) {
      return undefined;
    }
    let alive = true;
    loadDatalist(field.datalist).then((opts) => {
      if (alive) {
        setDatalistOptions(opts);
      }
    });
    return () => { alive = false; };
  }, [field.datalist]);
  const datalistId = field.datalist ? `datalist-${field.key}` : undefined;

  // The ⓘ tooltip carries the explanation AND the env-var name — the raw
  // key is not printed next to the label.
  const tipText = [
    field.help,
    field.warning && `⚠ ${field.warning}`,
    field.danger && `⛔ ${field.danger}`,
    `Environment variable: ${field.key}`,
  ].filter(Boolean).join('\n\n');

  return (
    <>
    <label
      data-field-key={field.key}
      className={[
        'settings-drawer-field',
        field.danger ? 'is-danger' : '',
        isBool ? 'is-toggle-row' : '',
      ].filter(Boolean).join(' ')}
    >
      <span className="settings-drawer-field-label">
        <span className="settings-drawer-field-name">{field.label}</span>
        {field.source && (
          <span className={`settings-drawer-source source-${field.source}`}>
            {sourceLabel(field.source)}
          </span>
        )}
        <FieldInfoTip text={tipText} />
      </span>

      {isBool ? (
        <input
          type="checkbox"
          className="settings-drawer-toggle"
          checked={boolChecked}
          onChange={(e) => onChange(e.target.checked ? 'true' : 'false')}
        />
      ) : isSelect ? (
        <select
          className="settings-drawer-input"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        >
          {(field.options || []).map((opt) => (
            <option key={opt} value={opt}>
              {opt === '' ? '(default)' : opt}
            </option>
          ))}
        </select>
      ) : (
        <input
          type={isSecret ? 'password' : (isNumber ? 'number' : 'text')}
          className="settings-drawer-input"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={(isSecret && value) ? '(set — paste to replace)' : (field.placeholder || fieldPlaceholder(field.key))}
          list={datalistId}
          spellCheck={false}
          autoComplete="off"
          autoCapitalize="off"
          autoCorrect="off"
        />
      )}
      {datalistId && (
        <datalist id={datalistId}>
          {datalistOptions.map((opt) => (
            <option key={opt.id} value={opt.id}>{opt.label}</option>
          ))}
        </datalist>
      )}

      {field.warning && (
        <span className="settings-drawer-field-warning">⚠ {field.warning}</span>
      )}
      {field.danger && (
        <span className="settings-drawer-field-danger">⛔ {field.danger}</span>
      )}
    </label>
    </>
  );
}

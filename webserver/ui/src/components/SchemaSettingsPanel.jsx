import { useCallback, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { fetchAllSettings, updateAllSettings, fetchOpenRouterModels } from '../api.js';
import { useRestartingSave } from '../hooks/useRestartingSave.js';
import { useSettingsResource } from '../hooks/useSettingsResource.js';
import { sourceLabel } from '../utils/settingsSource.js';
import { countNoun } from '../utils/pluralize.js';
import PanelMessage from './settings/PanelMessage.jsx';
import SettingsPanelHead from './settings/SettingsPanelHead.jsx';
import SettingsActions from './settings/SettingsActions.jsx';
import RestartBanner from './settings/RestartBanner.jsx';

// Generic, schema-driven settings panel. One instance renders ONE
// section of the ``/api/all-settings`` schema (General, Claude
// agent, Sandbox, Security scanner, Email & Slack, OpenHands,
// Docker/infra, AWS). Field widgets are chosen from ``field.type``;
// ``warning`` / ``danger`` annotations render inline. The section's
// own ``warning`` renders as a banner (the Sandbox tab uses this).
//
// Writes go to ~/.kato/settings.json via POST /api/all-settings
// (server whitelists to the schema). The operator's .env is never
// touched. Restart required — banner shown after a save.

export default function SchemaSettingsPanel({ sectionId }) {
  const [meta, setMeta] = useState({ sections: [], settingsFilePath: '' });
  const [draft, setDraft] = useState({});

  const { loading, error, refresh } = useSettingsResource(fetchAllSettings, (body) => {
    const sections = Array.isArray(body.sections) ? body.sections : [];
    setMeta({ sections, settingsFilePath: String(body.settings_file_path || '') });
    // Seed the draft from server values for THIS section's fields.
    const section = sections.find((s) => s.id === sectionId);
    const seed = {};
    for (const f of (section?.fields || [])) {
      seed[f.key] = f.value ?? '';
    }
    setDraft(seed);
  });

  const section = useMemo(
    () => meta.sections.find((s) => s.id === sectionId) || null,
    [meta.sections, sectionId],
  );

  const dirtyKeys = useMemo(() => {
    if (!section) { return []; }
    const out = [];
    for (const f of section.fields) {
      const server = f.value ?? '';
      const current = draft[f.key] ?? '';
      if (String(current) !== String(server)) { out.push(f.key); }
    }
    return out;
  }, [section, draft]);

  function setField(key, value) {
    setDraft((cur) => ({ ...cur, [key]: value }));
  }

  const { saving, savedAt, save } = useRestartingSave(
    () => {
      const updates = {};
      for (const k of dirtyKeys) { updates[k] = draft[k]; }
      return updateAllSettings(updates);
    },
    { onSaved: refresh },
  );

  function revert() {
    if (!section) { return; }
    const seed = {};
    for (const f of section.fields) { seed[f.key] = f.value ?? ''; }
    setDraft(seed);
  }

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
          {' '}<code>{meta.settingsFilePath || '~/.kato/settings.json'}</code>
          {' '}— your <code>.env</code> is left untouched (read as a
          fallback).
        </p>
      </SettingsPanelHead>

      {section.warning && (
        <div className="settings-drawer-section-warning">
          ⚠ {section.warning}
        </div>
      )}

      <div className="settings-drawer-fields">
        {section.fields.map((f) => (
          <SchemaField
            key={f.key}
            field={f}
            value={draft[f.key] ?? ''}
            onChange={(v) => setField(f.key, v)}
          />
        ))}
      </div>

      <SettingsActions
        onSecondary={revert}
        secondaryDisabled={saving || dirtyKeys.length === 0}
        onSave={save}
        saving={saving}
        canSave={dirtyKeys.length > 0}
        primaryLabel={dirtyKeys.length
          ? `Save ${countNoun(dirtyKeys.length, 'change')}`
          : 'Save'}
      />

      <RestartBanner show={savedAt} />
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
  const [tipPos, setTipPos] = useState(null);

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

  const tipText = [field.help, field.warning && `⚠ ${field.warning}`, field.danger && `⛔ ${field.danger}`].filter(Boolean).join('\n\n');

  const showTip = useCallback((e) => {
    const r = e.currentTarget.getBoundingClientRect();
    setTipPos({ x: r.left + r.width / 2, y: r.top });
  }, []);
  const hideTip = useCallback(() => setTipPos(null), []);

  return (
    <>
    <label
      className={[
        'settings-drawer-field',
        field.danger ? 'is-danger' : '',
        isBool ? 'is-toggle-row' : '',
      ].filter(Boolean).join(' ')}
    >
      <span className="settings-drawer-field-label">
        <code>{field.key}</code>
        <span className="settings-drawer-field-name">{field.label}</span>
        {field.source && (
          <span className={`settings-drawer-source source-${field.source}`}>
            {sourceLabel(field.source)}
          </span>
        )}
        {tipText && (
          <span
            className="settings-drawer-field-info"
            tabIndex={0}
            role="img"
            aria-label="Field info"
            onMouseEnter={showTip}
            onMouseLeave={hideTip}
            onFocus={showTip}
            onBlur={hideTip}
          >
            ⓘ
          </span>
        )}
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
          placeholder={(isSecret && value) ? '(set — paste to replace)' : (field.placeholder || '')}
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

      {field.help && (
        <span className="settings-drawer-field-hint">{field.help}</span>
      )}
      {field.warning && (
        <span className="settings-drawer-field-warning">⚠ {field.warning}</span>
      )}
      {field.danger && (
        <span className="settings-drawer-field-danger">⛔ {field.danger}</span>
      )}
    </label>
    {tipPos && tipText && createPortal(
      <div
        className="settings-field-tooltip"
        style={{ left: tipPos.x, top: tipPos.y }}
      >
        {tipText}
      </div>,
      document.body
    )}
    </>
  );
}

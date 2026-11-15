import { useEffect, useState } from 'react';
import { useSettingsResource } from '../hooks/useSettingsResource.js';
import { useRestartingSave } from '../hooks/useRestartingSave.js';
import {
  connectionFixes, isSecretKey, isWorkflowField, buildDraftFor, orderedFieldKeys, orderedProviders, providerOptionLabel,
} from '../utils/providerFields.js';
import { sourceLabel } from '../utils/settingsSource.js';
import { humanizeFieldKey, fieldPlaceholder, fieldInfo } from '../utils/fieldHelp.js';
import { credentialGuideFor } from '../utils/credentialGuides.js';
import {
  credentialKeysFor,
  isCredentialSourceKey,
  usesDiscoveredCredential,
} from '../utils/credentialSourceFields.js';
import FieldInfoTip from './settings/FieldInfoTip.jsx';
import CredentialGuide from './settings/CredentialGuide.jsx';
import CredentialSourcePicker from './settings/CredentialSourcePicker.jsx';
import SettingsPanelBody from './settings/SettingsPanelBody.jsx';
import SettingsPanelHead from './settings/SettingsPanelHead.jsx';
import SettingsActions from './settings/SettingsActions.jsx';
import ConnectionTest from './settings/ConnectionTest.jsx';
import { recheckConnections } from '../api.js';
import { SAVE_TO_APPLY, recheckOutcome } from '../utils/connectionRecheck.js';

// Shared credentials panel for the "Task provider" and "Git provider"
// settings tabs — they were ~90% identical (same state shape, fetch +
// draft seeding, dirty diff, save-with-restart, and field-list JSX). The
// only real differences are the API fns, the label map, the default
// provider, the header/select copy, and whether the panel also writes
// the *active* provider: Task provider does (it sets which platform kato
// polls); Git provider infers the host from each repo's remote URL, so
// it only edits creds. ``includeActive`` toggles that one branch.
//
// ``description`` is the lead sentence(s) of the header copy (a node);
// the shared "saved to <path>" tail is appended here. ``testFn`` (optional)
// adds "Test connection", which tests what the form holds BEFORE Save.
export default function ProviderCredentialsPanel({
  fetchFn,
  updateFn,
  labels,
  defaultProvider,
  title,
  description,
  selectLabel,
  selectHint,
  loadingMessage,
  includeActive = false,
  testFn = null,
}) {
  const [meta, setMeta] = useState({
    active: '', supported: [], providers: {}, settingsFilePath: '',
  });
  const [selected, setSelected] = useState('');
  const [draft, setDraft] = useState({});

  const { loading, error, refresh } = useSettingsResource(fetchFn, (body) => {
    // Set-up hosts first, so the tab opens on one the operator uses.
    const supported = orderedProviders(body.supported, body.providers);
    const active = includeActive
      ? String(body.active || supported[0] || defaultProvider)
      : '';
    const fallback = includeActive ? active : (supported[0] || defaultProvider);
    setMeta({
      active,
      supported,
      providers: body.providers || {},
      settingsFilePath: String(body.settings_file_path || ''),
    });
    setSelected((current) => current || fallback);
    setDraft(buildDraftFor(body.providers || {}, selected || fallback));
  });

  // Re-seed the draft from the chosen provider's server values whenever
  // the operator switches providers in the dropdown.
  useEffect(() => {
    if (!selected) { return; }
    setDraft(buildDraftFor(meta.providers, selected));
  }, [selected, meta.providers]);

  const isDirty = Object.entries(draft).some(([key, value]) => {
    const serverValue = meta.providers?.[selected]?.fields?.[key]?.value || '';
    return value !== serverValue;
  }) || (includeActive && selected && selected !== meta.active);

  // A good test and a save both go through kato's ONE apply-and-recheck
  // (connection_recheck.py): kato restarts with saved settings, or checks
  // again — and the banner follows that same outcome.
  const [applied, setApplied] = useState(null);
  const applyNow = async () => setApplied(recheckOutcome(await recheckConnections()));
  const { saving, save } = useRestartingSave(
    () => updateFn(includeActive
      ? { active: selected, provider: selected, fields: draft }
      : { provider: selected, fields: draft }),
    {
      onSaved: () => { refresh(); applyNow(); },
      successMessage: 'Saved — kato is putting it to work.',
      useServerMessage: false,
    },
  );

  const fields = meta.providers?.[selected]?.fields || {};
  // The credential source is picker-owned, never a typed input; and the
  // token input only appears when the operator chose to paste one.
  const credentialKeys = credentialKeysFor(selected);
  const isPasting = !usesDiscoveredCredential(draft[credentialKeys.source]);
  // In the server's order (most needed first); the optional workflow states
  // folded away below the connection test.
  const visibleFieldKeys = orderedFieldKeys(meta.providers?.[selected]?.order, Object.keys(fields)).filter((key) => (
    !isCredentialSourceKey(key) && !(!isPasting && key === credentialKeys.token)
  ));
  const mainFieldKeys = visibleFieldKeys.filter((key) => !isWorkflowField(key));
  const workflowFieldKeys = visibleFieldKeys.filter(isWorkflowField);
  const renderField = (key) => (
    <ProviderField
      key={key}
      fieldKey={key}
      field={fields[key] || {}}
      provider={selected}
      value={draft[key] || ''}
      onChange={(value) => setDraft((current) => ({ ...current, [key]: value }))}
    />
  );

  return (
    <div className="settings-drawer-panel">
      <SettingsPanelHead title={title}>
        <p>
          {description}
          {' '}<code>{meta.settingsFilePath || '~/.kato/settings.json'}</code>
          {' '}— kato&apos;s only config file.
        </p>
      </SettingsPanelHead>

      <SettingsPanelBody loading={loading} error={error} loadingMessage={loadingMessage}>
        <>
          <label className="settings-drawer-field">
            <span className="settings-drawer-field-label">{selectLabel}</span>
            <select
              className="settings-drawer-input"
              value={includeActive ? (selected || meta.active) : selected}
              onChange={(ev) => setSelected(ev.target.value)}
            >
              {meta.supported.map((name) => (
                <option key={name} value={name}>
                  {providerOptionLabel(name, labels, meta.providers)}
                </option>
              ))}
            </select>
            <span className="settings-drawer-field-hint">{selectHint}</span>
          </label>

          {/* At the top (operator's ask): test what the form holds, before Save,
              without scrolling past every field first. */}
          {testFn && (
            <ConnectionTest
              key={selected}
              label={labels[selected] || selected}
              test={() => testFn({ provider: selected, fields: draft })}
              fixesFor={(text) => connectionFixes(selected, text)}
              onApply={(fix) => setDraft((current) => ({ ...current, [fix.key]: fix.value }))}
              onConnected={() => (isDirty ? setApplied(SAVE_TO_APPLY) : applyNow())}
            />
          )}
          {applied && (
            <p className={`connection-apply connection-apply--${applied.kind}`} role="status">{applied.text}</p>
          )}

          <div className="settings-drawer-divider" />

          {/* Reuse an existing login instead of a pasted token — same
              picker as the wizard, so a token can also be swapped for a
              CLI login later without re-running setup. */}
          <CredentialSourcePicker
            provider={selected}
            providerLabel={labels[selected] || selected}
            value={draft[credentialKeys.source] || ''}
            onChange={(id) => setDraft((current) => ({
              ...current, [credentialKeys.source]: id,
            }))}
          />

          {/* Same "why + where do I get it" card the first-run wizard
              shows — collapsed here, because rotating a token months
              later is exactly when the menu path is forgotten again. */}
          {isPasting && (
            <CredentialGuide
              guide={credentialGuideFor(selected)}
              settingsFilePath={meta.settingsFilePath}
            />
          )}

          <div className="settings-drawer-fields">
            {mainFieldKeys.map(renderField)}
          </div>

          {workflowFieldKeys.length > 0 && (
            <details className="settings-drawer-advanced">
              <summary>
                Workflow states
                <span className="settings-drawer-advanced-hint">optional — empty uses the default shown</span>
              </summary>
              <div className="settings-drawer-fields">
                {workflowFieldKeys.map(renderField)}
              </div>
            </details>
          )}

          <SettingsActions
            onSecondary={refresh}
            secondaryLabel="Revert"
            onSave={save}
            saving={saving}
            canSave={isDirty}
            primaryLabel="Save"
          />

        </>
      </SettingsPanelBody>
    </div>
  );
}

// One provider field: its name, where its value comes from, the hint, and the
// input — a secret as a password, an empty one showing the default it means.
function ProviderField({ fieldKey, field, provider, value, onChange }) {
  const isSecret = isSecretKey(fieldKey);
  const placeholder = isSecret && field.value
    ? '(set — paste again to replace)'
    : (field.default || fieldPlaceholder(fieldKey));
  return (
    <label className="settings-drawer-field">
      <span className="settings-drawer-field-label">
        <span className="settings-drawer-field-name">
          {humanizeFieldKey(fieldKey, provider)}
        </span>
        <span className={`settings-drawer-source source-${field.source || 'unset'}`}>
          {sourceLabel(field.source)}
        </span>
        <FieldInfoTip text={fieldInfo(fieldKey)} />
      </span>
      <input
        type={isSecret ? 'password' : 'text'}
        className="settings-drawer-input"
        value={value}
        onChange={(ev) => onChange(ev.target.value)}
        placeholder={placeholder}
        spellCheck={false}
        autoComplete="off"
        autoCapitalize="off"
        autoCorrect="off"
      />
    </label>
  );
}

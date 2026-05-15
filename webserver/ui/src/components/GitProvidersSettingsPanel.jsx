import { useCallback, useEffect, useState } from 'react';
import { fetchGitProviders, updateGitProvider } from '../api.js';
import { toast } from '../stores/toastStore.js';

// "Git provider" tab — credentials kato uses to clone / push /
// open PRs. Only the real git hosts (Bitbucket / GitHub / GitLab);
// YouTrack + Jira are pure trackers and live on the Task provider
// tab. NO active selector here — kato infers the host from each
// repo's remote URL, so this tab only edits connection creds. The
// "Host" dropdown just picks WHICH host's creds to edit, it does
// NOT change which platform kato polls.

const HOST_LABELS = {
  bitbucket: 'Bitbucket',
  github: 'GitHub',
  gitlab: 'GitLab',
};

function isSecretKey(key) {
  const lower = key.toLowerCase();
  return lower.includes('token')
    || lower.includes('secret')
    || lower.includes('password');
}

export default function GitProvidersSettingsPanel() {
  const [state, setState] = useState({
    loading: true,
    error: '',
    supported: [],
    providers: {},
    settingsFilePath: '',
  });
  const [selected, setSelected] = useState('');
  const [draft, setDraft] = useState({});
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState(null);

  const refresh = useCallback(async () => {
    setState((prev) => ({ ...prev, loading: true, error: '' }));
    const result = await fetchGitProviders();
    if (!result.ok) {
      setState({
        loading: false,
        error: String(result.body?.error || result.error || 'load failed'),
        supported: [], providers: {}, settingsFilePath: '',
      });
      return;
    }
    const body = result.body || {};
    const supported = Array.isArray(body.supported) ? body.supported : [];
    setState({
      loading: false,
      error: '',
      supported,
      providers: body.providers || {},
      settingsFilePath: String(body.settings_file_path || body.env_file_path || ''),
    });
    setSelected((current) => current || supported[0] || 'bitbucket');
    setDraft(buildDraftFor(body.providers || {}, (selected || supported[0] || 'bitbucket')));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    if (!selected) { return; }
    setDraft(buildDraftFor(state.providers, selected));
  }, [selected, state.providers]);

  const isDirty = Object.entries(draft).some(([key, value]) => {
    const serverValue = state.providers?.[selected]?.fields?.[key]?.value || '';
    return value !== serverValue;
  });

  async function save() {
    setSaving(true);
    try {
      const result = await updateGitProvider({
        provider: selected,
        fields: draft,
      });
      if (!result.ok) {
        toast.show({
          kind: 'error',
          title: 'Save failed',
          message: String(result.body?.error || result.error || 'save failed'),
          durationMs: 8000,
        });
        return;
      }
      toast.show({
        kind: 'success',
        title: 'Saved',
        message: result.body?.message
          || 'Restart kato for the change to take effect.',
        durationMs: 7000,
      });
      setSavedAt(Date.now());
      refresh();
    } finally {
      setSaving(false);
    }
  }

  const fields = state.providers?.[selected]?.fields || {};

  return (
    <div className="settings-drawer-panel">
      <header className="settings-drawer-panel-head">
        <h3>Git provider</h3>
        <p>
          Credentials kato uses to clone, push branches, and open PRs.
          Kato picks the host automatically from each repo's remote
          URL — this just sets the creds per host. Saved to
          {' '}<code>{state.settingsFilePath || '~/.kato/settings.json'}</code>
          {' '}(your <code>.env</code> is left untouched — kato still
          reads it as a fallback).
        </p>
      </header>

      {state.loading && (
        <p className="settings-drawer-message">Loading git hosts…</p>
      )}
      {state.error && (
        <p className="settings-drawer-message is-error">{state.error}</p>
      )}

      {!state.loading && !state.error && (
        <>
          <label className="settings-drawer-field">
            <span className="settings-drawer-field-label">Host</span>
            <select
              className="settings-drawer-input"
              value={selected}
              onChange={(ev) => setSelected(ev.target.value)}
            >
              {state.supported.map((name) => (
                <option key={name} value={name}>
                  {HOST_LABELS[name] || name}
                </option>
              ))}
            </select>
            <span className="settings-drawer-field-hint">
              Picking a host here only chooses which creds to edit —
              it does NOT change which platform kato polls (that's
              the Task provider tab).
            </span>
          </label>

          <div className="settings-drawer-divider" />

          <div className="settings-drawer-fields">
            {Object.keys(fields).map((key) => {
              const f = fields[key] || {};
              const isSecret = isSecretKey(key);
              const placeholder = isSecret && f.value
                ? '(set — paste again to replace)'
                : '';
              return (
                <label key={key} className="settings-drawer-field">
                  <span className="settings-drawer-field-label">
                    <code>{key}</code>
                    <span className={`settings-drawer-source source-${f.source || 'unset'}`}>
                      {sourceLabel(f.source)}
                    </span>
                  </span>
                  <input
                    type={isSecret ? 'password' : 'text'}
                    className="settings-drawer-input"
                    value={draft[key] || ''}
                    onChange={(ev) =>
                      setDraft((current) => ({ ...current, [key]: ev.target.value }))
                    }
                    placeholder={placeholder}
                    spellCheck={false}
                    autoComplete="off"
                    autoCapitalize="off"
                    autoCorrect="off"
                  />
                </label>
              );
            })}
          </div>

          <div className="settings-drawer-actions">
            <button
              type="button"
              className="settings-drawer-action-secondary"
              onClick={refresh}
              disabled={saving}
            >
              Revert
            </button>
            <button
              type="button"
              className="settings-drawer-action-primary"
              onClick={save}
              disabled={!isDirty || saving}
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>

          {savedAt && (
            <div className="settings-drawer-restart-banner">
              ⚠ Restart kato for the change to take effect.
            </div>
          )}
        </>
      )}
    </div>
  );
}


function buildDraftFor(providers, name) {
  const fields = (providers?.[name]?.fields) || {};
  const out = {};
  for (const key of Object.keys(fields)) {
    out[key] = fields[key]?.value || '';
  }
  return out;
}

function sourceLabel(source) {
  if (source === 'env') { return 'live'; }
  if (source === 'kato_settings') { return 'saved'; }
  if (source === 'env_file') { return '.env'; }
  return 'unset';
}

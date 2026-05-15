import { useCallback, useEffect, useState } from 'react';
import { fetchSettings, updateSettings } from '../api.js';
import { toast } from '../stores/toastStore.js';

// "Repositories" tab inside the SettingsDrawer. Operator-editable
// REPOSITORY_ROOT_PATH — the folder kato walks for ``.git`` to
// auto-discover repos.
//
// Saved to ``~/.kato/settings.json`` via POST /api/settings. The
// operator's ``<repo>/.env`` is left untouched (kato still reads it
// as a fallback). The change is load-bearing at boot, so we surface
// "restart required" prominently after every successful save.

export default function RepositoriesSettingsPanel() {
  const [state, setState] = useState({
    loading: true,
    error: '',
    value: '',
    source: 'unset',
    settingsFilePath: '',
  });
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState(null);

  const refresh = useCallback(async () => {
    setState((prev) => ({ ...prev, loading: true, error: '' }));
    const result = await fetchSettings();
    if (!result.ok) {
      setState({
        loading: false,
        error: String(result.error || result.body?.error || 'load failed'),
        value: '', source: 'unset', settingsFilePath: '',
      });
      return;
    }
    const repo = result.body?.repository_root_path || {};
    setState({
      loading: false,
      error: '',
      value: String(repo.value || ''),
      source: String(repo.source || 'unset'),
      settingsFilePath: String(
        result.body?.settings_file_path || result.body?.env_file_path || '',
      ),
    });
    setDraft(String(repo.value || ''));
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  async function save() {
    const trimmed = draft.trim();
    if (!trimmed) {
      toast.show({
        kind: 'error',
        title: 'Empty path',
        message: 'Enter a folder path before saving.',
      });
      return;
    }
    setSaving(true);
    try {
      const result = await updateSettings({ repository_root_path: trimmed });
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

  const dirty = draft.trim() !== state.value;
  let sourceLabel = 'Unset';
  if (state.source === 'env') {
    sourceLabel = 'Live (process env)';
  } else if (state.source === 'kato_settings') {
    sourceLabel = 'Saved (~/.kato/settings.json)';
  } else if (state.source === 'env_file') {
    sourceLabel = 'From .env file (legacy fallback)';
  }

  return (
    <div className="settings-drawer-panel">
      <header className="settings-drawer-panel-head">
        <h3>Repositories</h3>
        <p>
          The folder kato walks for ``.git`` directories to
          auto-discover repos. Saved to
          {' '}<code>{state.settingsFilePath || '~/.kato/settings.json'}</code>
          {' '}as <code>REPOSITORY_ROOT_PATH</code> (your <code>.env</code>
          {' '}is left untouched — kato still reads it as a fallback).
        </p>
      </header>

      {state.loading && (
        <p className="settings-drawer-message">Loading current setting…</p>
      )}
      {state.error && (
        <p className="settings-drawer-message is-error">{state.error}</p>
      )}

      {!state.loading && !state.error && (
        <>
          <label className="settings-drawer-field">
            <span className="settings-drawer-field-label">Folder path</span>
            <input
              type="text"
              className="settings-drawer-input"
              placeholder="/Users/you/projects"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              spellCheck={false}
              autoCapitalize="off"
              autoCorrect="off"
            />
            <span className="settings-drawer-field-hint">
              Tip: paste an absolute path, or <code>~/Projects</code> —
              kato expands ``~`` and resolves relative segments on save.
            </span>
          </label>

          <div className="settings-drawer-status-row">
            <span className="settings-drawer-kv">
              <span className="settings-drawer-kv-key">Current</span>
              <code className="settings-drawer-kv-value">
                {state.value || '(unset)'}
              </code>
            </span>
            <span className="settings-drawer-kv">
              <span className="settings-drawer-kv-key">Source</span>
              <span className={`settings-drawer-kv-value source-${state.source}`}>
                {sourceLabel}
              </span>
            </span>
          </div>

          <div className="settings-drawer-actions">
            <button
              type="button"
              className="settings-drawer-action-secondary"
              onClick={() => setDraft(state.value)}
              disabled={!dirty || saving}
            >
              Revert
            </button>
            <button
              type="button"
              className="settings-drawer-action-primary"
              onClick={save}
              disabled={!dirty || saving}
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

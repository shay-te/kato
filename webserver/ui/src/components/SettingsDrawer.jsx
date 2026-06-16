import { useEffect, useState } from 'react';
import ActionGuardSettingsPanel from './ActionGuardSettingsPanel.jsx';
import ClaudePermissionsSettingsPanel from './ClaudePermissionsSettingsPanel.jsx';
import PromptsSettingsPanel from './PromptsSettingsPanel.jsx';
import GitProvidersSettingsPanel from './GitProvidersSettingsPanel.jsx';
import NotificationsSettingsPanel from './NotificationsSettingsPanel.jsx';
import RepositoriesSettingsPanel from './RepositoriesSettingsPanel.jsx';
import RepositoryApprovalsSettingsPanel from './RepositoryApprovalsSettingsPanel.jsx';
import SchemaSettingsPanel from './SchemaSettingsPanel.jsx';
import TaskProviderSettingsPanel from './TaskProviderSettingsPanel.jsx';
import { fetchAllSettings } from '../api.js';
import { useEscapeKey } from '../hooks/useEscapeKey.js';
import { cx } from '../utils/cx.js';
import { buildSettingsIndex, filterSettingsIndex } from '../utils/settingsSearch.js';

// Right-side drawer hosting every operator-editable setting under
// tabs. Five tabs have bespoke logic (provider switchers, the
// approvals table, repo-root path validation, notification
// toggles); the rest are DATA-DRIVEN — one tab per section of the
// ``/api/all-settings`` schema, rendered by the generic
// SchemaSettingsPanel. Adding a new env setting = one entry in
// kato_settings_schema.py, no UI change.

const TAB_REPOS = 'repositories';
const TAB_APPROVALS = 'approvals';
const TAB_PERMISSIONS = 'claude-permissions';
const TAB_ACTION_GUARD = 'action-guard';
const TAB_PROMPTS = 'prompts';
const TAB_TASK_PROVIDER = 'task-provider';
const TAB_GIT_PROVIDER = 'git-provider';
const TAB_NOTIFICATIONS = 'notifications';

// The action_guard schema section is rendered by its OWN bespoke tab below,
// so it's excluded from the auto-generated schema tabs to avoid a duplicate.
const ACTION_GUARD_SECTION_ID = 'action_guard';

// Bespoke (non-schema) tabs, in display order.
const BESPOKE_TABS = [
  { id: TAB_REPOS, label: 'Repositories' },
  { id: TAB_APPROVALS, label: 'Approvals' },
  { id: TAB_PERMISSIONS, label: 'Permissions' },
  { id: TAB_ACTION_GUARD, label: 'Action Guard' },
  { id: TAB_PROMPTS, label: 'Prompts' },
  { id: TAB_TASK_PROVIDER, label: 'Task provider' },
  { id: TAB_GIT_PROVIDER, label: 'Git provider' },
  { id: TAB_NOTIFICATIONS, label: 'Notifications' },
];

export default function SettingsDrawer({
  open,
  onClose,
  notificationProps,
}) {
  const [tab, setTab] = useState(TAB_REPOS);
  // Raw schema sections (with fields) for the data-driven tabs AND the
  // cross-tab search index. Fetched once when the drawer first opens.
  const [schemaSections, setSchemaSections] = useState([]);
  const [schemaLoaded, setSchemaLoaded] = useState(false);
  // "Find a setting" query + the field to scroll-highlight after a jump.
  const [query, setQuery] = useState('');
  const [highlightKey, setHighlightKey] = useState('');

  useEffect(() => {
    if (!open || schemaLoaded) { return; }
    let cancelled = false;
    fetchAllSettings().then((result) => {
      if (cancelled) { return; }
      const sections = Array.isArray(result.body?.sections)
        ? result.body.sections
        : [];
      setSchemaSections(sections);
      setSchemaLoaded(true);
    });
    return () => { cancelled = true; };
  }, [open, schemaLoaded]);

  // Exclude the Action Guard section from the generic schema tabs + search
  // index — it has its own bespoke tab (findable by the "Action Guard" label).
  const genericSchemaSections = schemaSections.filter(
    (s) => s.id !== ACTION_GUARD_SECTION_ID,
  );
  const schemaTabs = genericSchemaSections.map((s) => ({
    id: `schema:${s.id}`, sectionId: s.id, label: s.label,
  }));
  const searchResults = filterSettingsIndex(
    buildSettingsIndex(genericSchemaSections, BESPOKE_TABS), query,
  );

  function jumpToSetting(result) {
    setTab(result.tabId);
    setHighlightKey(result.kind === 'field' ? result.key : '');
    setQuery('');
  }

  // ESC closes the drawer. Bound only while open so other ESC
  // consumers (chat search, modals) aren't double-fired.
  useEscapeKey(onClose, open);

  const drawerClass = cx('settings-drawer', open ? 'is-open' : '');
  const backdropClass = cx('settings-drawer-backdrop', open ? 'is-open' : '');

  let panel;
  if (tab === TAB_REPOS) {
    panel = <RepositoriesSettingsPanel />;
  } else if (tab === TAB_APPROVALS) {
    panel = <RepositoryApprovalsSettingsPanel />;
  } else if (tab === TAB_PERMISSIONS) {
    panel = <ClaudePermissionsSettingsPanel />;
  } else if (tab === TAB_ACTION_GUARD) {
    panel = <ActionGuardSettingsPanel />;
  } else if (tab === TAB_PROMPTS) {
    panel = <PromptsSettingsPanel />;
  } else if (tab === TAB_TASK_PROVIDER) {
    panel = <TaskProviderSettingsPanel />;
  } else if (tab === TAB_GIT_PROVIDER) {
    panel = <GitProvidersSettingsPanel />;
  } else if (tab === TAB_NOTIFICATIONS) {
    panel = <NotificationsSettingsPanel {...(notificationProps || {})} />;
  } else if (tab.startsWith('schema:')) {
    const sectionId = tab.slice('schema:'.length);
    panel = (
      <SchemaSettingsPanel
        key={sectionId}
        sectionId={sectionId}
        highlightKey={highlightKey}
      />
    );
  }

  const allTabs = [...BESPOKE_TABS, ...schemaTabs];

  return (
    <>
      <div
        className={backdropClass}
        onClick={onClose}
        aria-hidden={!open}
      />
      <aside
        className={drawerClass}
        role="dialog"
        aria-label="Settings"
        aria-hidden={!open}
      >
        <header className="settings-drawer-head">
          <h2>Settings</h2>
          <button
            type="button"
            className="settings-drawer-close"
            onClick={onClose}
            aria-label="Close settings"
            title="Close (Esc)"
          >
            ×
          </button>
        </header>
        <div className="settings-drawer-search">
          <input
            type="search"
            className="settings-drawer-search-input"
            placeholder="Search settings…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search settings"
          />
          {query && (
            <div className="settings-drawer-search-results" role="listbox">
              {searchResults.length === 0 ? (
                <div className="settings-drawer-search-empty">
                  No settings match “{query}”.
                </div>
              ) : (
                searchResults.map((r) => (
                  <button
                    key={`${r.tabId}:${r.key || r.label}`}
                    type="button"
                    role="option"
                    className="settings-drawer-search-result"
                    onClick={() => jumpToSetting(r)}
                  >
                    <span className="settings-drawer-search-result-label">{r.label}</span>
                    {r.key && (
                      <code className="settings-drawer-search-result-key">{r.key}</code>
                    )}
                    <span className="settings-drawer-search-result-section">{r.section}</span>
                  </button>
                ))
              )}
            </div>
          )}
        </div>
        <nav className="settings-drawer-tabs" role="tablist">
          {allTabs.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              className={`settings-drawer-tab ${tab === t.id ? 'is-active' : ''}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </nav>
        <div className="settings-drawer-body">
          {panel}
        </div>
      </aside>
    </>
  );
}

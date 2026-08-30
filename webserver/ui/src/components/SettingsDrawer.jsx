import { useEffect, useState } from 'react';
import ActionGuardSettingsPanel from './ActionGuardSettingsPanel.jsx';
import ChatSettingsPanel from './ChatSettingsPanel.jsx';
import ClaudePermissionsSettingsPanel from './ClaudePermissionsSettingsPanel.jsx';
import PromptsSettingsPanel from './PromptsSettingsPanel.jsx';
import GitProvidersSettingsPanel from './GitProvidersSettingsPanel.jsx';
import NotificationsSettingsPanel from './NotificationsSettingsPanel.jsx';
import RepositoriesSettingsPanel from './RepositoriesSettingsPanel.jsx';
import RepositoryApprovalsSettingsPanel from './RepositoryApprovalsSettingsPanel.jsx';
import SchemaSettingsPanel from './SchemaSettingsPanel.jsx';
import TaskProviderSettingsPanel from './TaskProviderSettingsPanel.jsx';
import {
  loadAllSettings,
  subscribeAllSettings,
} from '../stores/allSettingsStore.js';
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
const TAB_CHAT = 'chat';

// The action_guard schema section is rendered by its OWN bespoke tab below,
// so it's excluded from the auto-generated schema tabs to avoid a duplicate.
const ACTION_GUARD_SECTION_ID = 'action_guard';

// Bespoke (non-schema) tabs, in display order.
//
// ``description`` is the hover tooltip — what you will find inside, so the
// operator can aim at a tab instead of opening each one to look. The
// schema-driven tabs get theirs from the schema itself (each section already
// declares one); only these hand-built tabs need it written out.
const BESPOKE_TABS = [
  {
    id: TAB_REPOS,
    label: 'Repositories',
    description: 'Which repositories kato works on, and the root path it '
      + 'scans to discover them.',
  },
  {
    id: TAB_APPROVALS,
    label: 'Approvals',
    description: 'Which repositories the agent is allowed to touch, and the '
      + 'scope granted to each.',
  },
  {
    id: TAB_PERMISSIONS,
    label: 'Permissions',
    description: 'Every remembered "Allow always" / "Deny always" tool '
      + 'decision. Revoke one here and it stops applying immediately.',
  },
  {
    id: TAB_ACTION_GUARD,
    label: 'Action Guard',
    description: 'What the agent is blocked from doing regardless of '
      + 'permission mode — the always-blocked floor and the per-category '
      + 'postures above it.',
  },
  {
    id: TAB_PROMPTS,
    label: 'Prompts',
    description: 'The prompt text kato sends the agent — task framing, '
      + 'review-comment handling, and the learned-lessons file.',
  },
  {
    id: TAB_TASK_PROVIDER,
    label: 'Task provider',
    description: 'Where tasks come from — YouTrack, Jira, Bitbucket, GitHub '
      + 'or GitLab — and the credentials for it.',
  },
  {
    id: TAB_GIT_PROVIDER,
    label: 'Git provider',
    description: 'Where branches are pushed and pull requests opened, and '
      + 'the token used to do it.',
  },
  {
    id: TAB_NOTIFICATIONS,
    label: 'Notifications',
    description: 'Which events raise a desktop notification — approval '
      + 'requests, finished turns, failures.',
  },
  {
    id: TAB_CHAT,
    label: 'Chat',
    description: 'How the composer behaves: whether a message sent mid-turn '
      + 'is queued or delivered immediately, and the ultracode default for '
      + 'new tasks.',
  },
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

  // Read through the shared store, and re-read whenever a panel saves.
  //
  // The load is still latched (``schemaLoaded``) so opening the drawer does
  // not refetch — but latched-and-never-unmounted was exactly the bug: this
  // component's ``open`` prop only drives a CSS transform, so the drawer lives
  // for the whole page. Its search index therefore served pre-save values
  // until a full reload. Subscribing to the store's invalidation is what
  // closes that; the cache is the cheap part.
  useEffect(() => {
    if (!open) { return undefined; }
    let cancelled = false;
    const read = () => {
      loadAllSettings().then((result) => {
        // A FAILED read must leave the last good index alone. ``requestEnvelope``
        // never rejects — a non-2xx resolves ``{ok:false, body:{error}}`` and a
        // network throw resolves ``{ok:false}`` with no body at all — so the
        // catch below is dead for the real failure modes, and taking the empty
        // branch would blank every schema tab AND the search index. With the
        // load latched and the drawer never unmounting, that state never
        // recovered short of a page reload. Now reachable on every save, since
        // the store notifies subscribers after each one.
        if (cancelled || !result?.ok) { return; }
        if (!Array.isArray(result.body?.sections)) { return; }
        setSchemaSections(result.body.sections);
        setSchemaLoaded(true);
      }).catch(() => { /* keep the last good index */ });
    };
    if (!schemaLoaded) { read(); }
    const unsubscribe = subscribeAllSettings(read);
    return () => { cancelled = true; unsubscribe(); };
  }, [open, schemaLoaded]);

  // Exclude the Action Guard section from the generic schema tabs + search
  // index — it has its own bespoke tab (findable by the "Action Guard" label).
  const genericSchemaSections = schemaSections.filter(
    (s) => s.id !== ACTION_GUARD_SECTION_ID,
  );
  const schemaTabs = genericSchemaSections.map((s) => ({
    id: `schema:${s.id}`,
    sectionId: s.id,
    label: s.label,
    // Straight from the schema — each section already declares what it
    // covers, so the tooltip stays correct as settings are added without
    // anyone remembering to update a second copy here.
    description: s.description || '',
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
    panel = <RepositoryApprovalsSettingsPanel highlightKey={highlightKey} />;
  } else if (tab === TAB_PERMISSIONS) {
    panel = <ClaudePermissionsSettingsPanel open={open} />;
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
  } else if (tab === TAB_CHAT) {
    panel = <ChatSettingsPanel />;
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
              // Native title rather than the app's data-tooltip CSS: this list
              // scrolls inside a narrow drawer, and a positioned
              // pseudo-element would be clipped by the nav's own overflow.
              title={t.description || undefined}
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

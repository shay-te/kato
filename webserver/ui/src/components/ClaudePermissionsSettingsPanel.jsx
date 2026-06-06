import { useEffect, useMemo, useState } from 'react';
import { toolDecisionsStore } from '../stores/toolDecisionsStore.js';
import { toast } from '../stores/toastStore.js';
import { filterPermissionRows } from './ClaudePermissionsHelpers.js';
import SettingsPanelBody from './settings/SettingsPanelBody.jsx';
import SettingsPanelHead from './settings/SettingsPanelHead.jsx';

// "Claude permissions" tab — lists every remembered "Allow always" /
// "Deny always" decision (the localStorage ``kato.toolDecisions.v1`` set).
// Tool-level tools (Edit, Read…) show one row; command-keyed tools (Bash)
// show ONE ROW PER PROGRAM (the command's program signature — `mvn`,
// `docker`, `ls` — not the verbatim, path-specific line), so the operator
// curates which programs auto-run (allowing `mvn` never allows `docker`). A
// filter box narrows a long list.
//
// No Save/diff cycle: changes persist instantly through the shared
// ``toolDecisionsStore``, so the permission prompt (same store) sees a
// revoke immediately — no stale auto-allow window. Subscribes so an
// approval granted from a live prompt appears here without a reload.

export default function ClaudePermissionsSettingsPanel() {
  const [rows, setRows] = useState(() => toolDecisionsStore.entries());
  const [filter, setFilter] = useState('');

  useEffect(
    () => toolDecisionsStore.subscribe(() => setRows(toolDecisionsStore.entries())),
    [],
  );

  const visible = useMemo(() => filterPermissionRows(rows, filter), [rows, filter]);

  function setScope(key, decision) {
    toolDecisionsStore.setDecisionByKey(key, decision);
  }

  function clearOne(key, label) {
    toolDecisionsStore.forgetByKey(key);
    toast.show({
      kind: 'info',
      title: 'Permission cleared',
      message: `kato will ask again next time: ${label}.`,
      durationMs: 4000,
    });
  }

  function clearAll() {
    toolDecisionsStore.forget();
    toast.show({
      kind: 'info',
      title: 'All permissions cleared',
      message: 'kato will ask again for every tool/command.',
      durationMs: 4000,
    });
  }

  const empty = rows.length === 0;
  const noMatches = !empty && visible.length === 0;

  return (
    <div className="settings-drawer-panel">
      <SettingsPanelHead title="Claude permissions">
        <p>
          The <strong>Allow always</strong> / <strong>Deny always</strong>{' '}
          decisions you gave Claude, remembered across kato and browser
          restarts. Bash entries are <strong>per program</strong> (e.g.{' '}
          <code>mvn</code>, <code>docker</code>) — allowing one program never
          allows another, and matches it in any task folder. Change the scope or
          clear one to be asked again. Requests that reach{' '}
          <strong>outside the task folder</strong> are never remembered here.
        </p>
      </SettingsPanelHead>

      <SettingsPanelBody>
        <>
          {empty ? (
            <p className="settings-drawer-message">
              No saved permissions yet. When you click{' '}
              <strong>Allow always</strong> on a permission prompt, it shows
              up here.
            </p>
          ) : (
            <>
              <input
                type="search"
                className="settings-drawer-input settings-perm-filter"
                placeholder="Filter by tool or command…"
                value={filter}
                onChange={(ev) => setFilter(ev.target.value)}
                spellCheck={false}
                aria-label="Filter saved permissions"
              />
              {noMatches ? (
                <p className="settings-drawer-message">No permissions match the filter.</p>
              ) : (
                <table className="settings-drawer-approvals-table">
                  <thead>
                    <tr>
                      <th>Tool / command</th>
                      <th>Scope</th>
                      <th aria-label="Actions" />
                    </tr>
                  </thead>
                  <tbody>
                    {visible.map(({ key, tool, command, decision }) => (
                      <tr key={key}>
                        <td>
                          <div className="settings-drawer-approval-id">{tool}</div>
                          {command && (
                            <code className="settings-perm-command" title={command}>
                              {command}
                            </code>
                          )}
                        </td>
                        <td>
                          <select
                            className="settings-drawer-input is-compact"
                            value={decision}
                            aria-label={`Scope for ${command || tool}`}
                            onChange={(ev) => setScope(key, ev.target.value)}
                          >
                            <option value="allow">always allow</option>
                            <option value="deny">always deny</option>
                          </select>
                        </td>
                        <td>
                          <button
                            type="button"
                            className="settings-drawer-perm-clear"
                            onClick={() => clearOne(key, command || tool)}
                          >
                            Clear
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              <div className="settings-drawer-actions">
                <button
                  type="button"
                  className="settings-drawer-action-secondary"
                  onClick={clearAll}
                >
                  Clear all ({rows.length})
                </button>
              </div>
            </>
          )}
        </>
      </SettingsPanelBody>
    </div>
  );
}

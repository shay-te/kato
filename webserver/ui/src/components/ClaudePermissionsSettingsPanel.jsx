import { useCallback, useMemo, useState } from 'react';
import {
  clearToolDecisions,
  fetchToolDecisions,
  forgetToolDecision,
  setToolDecision,
} from '../api.js';
import { usePolling } from '../hooks/usePolling.js';
import { toast } from '../stores/toastStore.js';
import { filterPermissionRows } from './ClaudePermissionsHelpers.js';
import SettingsPanelBody from './settings/SettingsPanelBody.jsx';
import SettingsPanelHead from './settings/SettingsPanelHead.jsx';

const POLL_INTERVAL_MS = 15_000;

// "Claude permissions" tab — lists every remembered "Allow always" /
// "Deny always" decision. Backend-owned (kato_core_lib/helpers/
// tool_decision_store.py is the sole source of truth; the browser holds
// no copy of its own) so a decision granted from ANY browser/device
// shows up here, and revoking it here takes effect immediately for the
// next pending ask regardless of which client made the original grant.
// Tool-level tools (Edit, Read…) show one row; command-keyed tools
// (Bash) show ONE ROW PER PROGRAM (the command's program signature —
// `mvn`, `docker`, `ls` — not the verbatim, path-specific line), so the
// operator curates which programs auto-run (allowing `mvn` never
// allows `docker`). A filter box narrows a long list.
export default function ClaudePermissionsSettingsPanel({ open = true }) {
  const [rows, setRows] = useState([]);
  const [filter, setFilter] = useState('');

  const refresh = useCallback(async () => {
    const result = await fetchToolDecisions();
    if (!result.ok || !Array.isArray(result.body?.decisions)) { return; }
    setRows(result.body.decisions.map((entry) => ({
      key: `${entry.tool_name}\u0000${entry.command_signature}`,
      tool: entry.tool_name,
      command: entry.command_signature,
      decision: entry.allow ? 'allow' : 'deny',
    })));
  }, []);

  // Gated on the drawer being OPEN, not just mounted. SettingsDrawer never
  // unmounts — ``open`` only drives a CSS transform — and the selected tab
  // survives closing, so a literal ``true`` here kept this polling for the
  // rest of the page's life whenever Permissions happened to be the last tab
  // viewed. Nothing was on screen to show for it. (usePolling's visibility
  // guard covers the hidden-window case; this covers the closed-drawer one.)
  usePolling(refresh, POLL_INTERVAL_MS, [], { enabled: open });

  const visible = useMemo(() => filterPermissionRows(rows, filter), [rows, filter]);

  async function setScope(row, decision) {
    const result = await setToolDecision(row.tool, row.command, decision === 'allow');
    if (result.ok) { refresh(); }
  }

  async function clearOne(row) {
    const result = await forgetToolDecision(row.tool, row.command);
    if (!result.ok) { return; }
    refresh();
    toast.show({
      kind: 'info',
      title: 'Permission cleared',
      message: `kato will ask again next time: ${row.command || row.tool}.`,
      durationMs: 4000,
    });
  }

  async function clearAll() {
    const result = await clearToolDecisions();
    if (!result.ok) { return; }
    refresh();
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
                    {visible.map((row) => (
                      <tr key={row.key}>
                        <td>
                          <div className="settings-drawer-approval-id">{row.tool}</div>
                          {row.command && (
                            <code className="settings-perm-command" title={row.command}>
                              {row.command}
                            </code>
                          )}
                        </td>
                        <td>
                          <select
                            className="settings-drawer-input is-compact"
                            value={row.decision}
                            aria-label={`Scope for ${row.command || row.tool}`}
                            onChange={(ev) => setScope(row, ev.target.value)}
                          >
                            <option value="allow">always allow</option>
                            <option value="deny">always deny</option>
                          </select>
                        </td>
                        <td>
                          <button
                            type="button"
                            className="settings-drawer-perm-clear"
                            onClick={() => clearOne(row)}
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

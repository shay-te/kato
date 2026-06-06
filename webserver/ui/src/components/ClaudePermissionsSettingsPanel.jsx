import { useEffect, useState } from 'react';
import { toolDecisionsStore } from '../stores/toolDecisionsStore.js';
import { toast } from '../stores/toastStore.js';
import SettingsPanelBody from './settings/SettingsPanelBody.jsx';
import SettingsPanelHead from './settings/SettingsPanelHead.jsx';

// "Claude permissions" tab — lists the remembered "Allow always" /
// "Deny always" decisions the operator gave Claude (the localStorage
// ``kato.toolDecisions.v1`` set), and lets them re-scope or clear each.
//
// Mirrors the Repository-approvals tab in shape, but there is no
// Save/diff cycle: every change applies + persists instantly through
// the shared ``toolDecisionsStore``, so the permission PROMPT (which
// reads the same store) sees a revoke the moment it happens — no stale
// auto-allow window. The panel subscribes to the store so an approval
// granted from a live prompt shows up here without a reload.

export default function ClaudePermissionsSettingsPanel() {
  const [rows, setRows] = useState(() => toolDecisionsStore.entries());

  useEffect(
    () => toolDecisionsStore.subscribe(() => setRows(toolDecisionsStore.entries())),
    [],
  );

  function setScope(tool, decision) {
    toolDecisionsStore.setDecision(tool, decision);
  }

  function clearOne(tool) {
    toolDecisionsStore.forget(tool);
    toast.show({
      kind: 'info',
      title: 'Permission cleared',
      message: `kato will ask again next time Claude uses ${tool}.`,
      durationMs: 4000,
    });
  }

  function clearAll() {
    toolDecisionsStore.forget();
    toast.show({
      kind: 'info',
      title: 'All permissions cleared',
      message: 'kato will ask again for every tool.',
      durationMs: 4000,
    });
  }

  return (
    <div className="settings-drawer-panel">
      <SettingsPanelHead title="Claude permissions">
        <p>
          The <strong>Allow always</strong> / <strong>Deny always</strong>{' '}
          decisions you gave Claude, remembered across kato and browser
          restarts. Change the scope or clear one to be asked again.
          Requests that reach <strong>outside the task folder</strong> are
          never remembered here — they always prompt.
        </p>
      </SettingsPanelHead>

      <SettingsPanelBody>
        <>
          {rows.length === 0 ? (
            <p className="settings-drawer-message">
              No saved permissions yet. When you click{' '}
              <strong>Allow always</strong> on a permission prompt, the tool
              shows up here.
            </p>
          ) : (
            <table className="settings-drawer-approvals-table">
              <thead>
                <tr>
                  <th>Tool</th>
                  <th>Scope</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {rows.map(({ tool, decision }) => (
                  <tr key={tool}>
                    <td>
                      <div className="settings-drawer-approval-id">{tool}</div>
                    </td>
                    <td>
                      <select
                        className="settings-drawer-input is-compact"
                        value={decision}
                        aria-label={`Scope for ${tool}`}
                        onChange={(ev) => setScope(tool, ev.target.value)}
                      >
                        <option value="allow">always allow</option>
                        <option value="deny">always deny</option>
                      </select>
                    </td>
                    <td>
                      <button
                        type="button"
                        className="settings-drawer-perm-clear"
                        onClick={() => clearOne(tool)}
                      >
                        Clear
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {rows.length > 0 && (
            <div className="settings-drawer-actions">
              <button
                type="button"
                className="settings-drawer-action-secondary"
                onClick={clearAll}
              >
                Clear all ({rows.length})
              </button>
            </div>
          )}
        </>
      </SettingsPanelBody>
    </div>
  );
}

import { useState } from 'react';
import {
  fetchRepositoryApprovals,
  updateRepositoryApprovals,
} from '../api.js';
import { toast } from '../stores/toastStore.js';
import { useSettingsResource } from '../hooks/useSettingsResource.js';
import SettingsPanelBody from './settings/SettingsPanelBody.jsx';
import SettingsPanelHead from './settings/SettingsPanelHead.jsx';
import SettingsActions from './settings/SettingsActions.jsx';
import SchemaFieldGroup from './settings/SchemaFieldGroup.jsx';

// "Repository approvals" tab — where repositories are approved (an old
// command-line picker used to do it). Lists every candidate kato
// discovers (inventory + checkout + workspace clones), shows which
// are approved + their mode, and lets the operator toggle approve /
// revoke + restricted ↔ trusted in batch.
//
// Save sends one POST with the diff against the loaded state, so a
// rapid toggle (approve → revoke → approve) ends with one network
// trip in the right final state.

const MODE_RESTRICTED = 'restricted';
const MODE_TRUSTED = 'trusted';

// A repository approved now is trusted unless the operator picks restricted:
// restricted also needs the Docker sandbox and a stricter scanner, which a new
// install has neither of, so it refused every task kato picked up by itself.
// An approval saved with no mode was stored as restricted, and still is.
function modeOf(row) {
  if (row && row.approval_mode) { return row.approval_mode; }
  return row && row.approved ? MODE_RESTRICTED : MODE_TRUSTED;
}

// ``showScope``: the ignore list and denylist below the table. The setup
// wizard leaves them out — a first run only needs to tick repositories.
export default function RepositoryApprovalsSettingsPanel({ highlightKey = '', showScope = true }) {
  const [meta, setMeta] = useState({ rows: [], storagePath: '' });
  // Per-row pending edits keyed by repository_id. Each value is
  // ``{ approved: bool, mode: 'restricted' | 'trusted' }``. We
  // compare against the original row on save to compute the
  // approve / revoke arrays.
  const [edits, setEdits] = useState({});
  const [saving, setSaving] = useState(false);

  const { loading, error, refresh } = useSettingsResource(fetchRepositoryApprovals, (body) => {
    setMeta({
      rows: Array.isArray(body?.repositories) ? body.repositories : [],
      storagePath: String(body?.storage_path || ''),
    });
    setEdits({});
  });

  function rowState(row) {
    const e = edits[row.repository_id];
    if (e) { return e; }
    return {
      approved: !!row.approved,
      mode: modeOf(row),
    };
  }

  function patchEdit(repoId, patch) {
    setEdits((current) => ({
      ...current,
      [repoId]: { ...rowStateFor(current, meta.rows, repoId), ...patch },
    }));
  }

  const hasChanges = meta.rows.some((row) => {
    const e = edits[row.repository_id];
    if (!e) { return false; }
    if ((!!e.approved) !== !!row.approved) { return true; }
    if (e.approved && e.mode !== modeOf(row)) {
      return true;
    }
    return false;
  });

  async function save() {
    const approve = [];
    const revoke = [];
    for (const row of meta.rows) {
      const e = edits[row.repository_id];
      if (!e) { continue; }
      const wasApproved = !!row.approved;
      const isApproved = !!e.approved;
      if (!wasApproved && isApproved) {
        approve.push({
          repository_id: row.repository_id,
          remote_url: row.remote_url || row.approved_remote_url,
          mode: e.mode,
        });
      } else if (wasApproved && !isApproved) {
        revoke.push(row.repository_id);
      } else if (wasApproved && isApproved && e.mode !== modeOf(row)) {
        approve.push({
          repository_id: row.repository_id,
          remote_url: row.remote_url || row.approved_remote_url,
          mode: e.mode,
        });
      }
    }
    if (approve.length === 0 && revoke.length === 0) { return; }
    setSaving(true);
    try {
      const result = await updateRepositoryApprovals({ approve, revoke });
      if (!result.ok) {
        toast.errorFromResult(result, {
          title: 'Save failed', fallback: 'save failed', durationMs: 8000,
        });
        return;
      }
      const counts = result.body?.applied || {};
      toast.show({
        kind: 'success',
        title: 'Saved',
        message: `${(counts.approved || []).length} approved, `
          + `${(counts.revoked || []).length} revoked.`,
        durationMs: 5000,
      });
      refresh();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="settings-drawer-panel">
      <SettingsPanelHead title="Repository approvals">
        <p>
          Kato works only in repositories approved here. Toggle one to approve it.
          <strong> trusted</strong> (the default): its tasks run.
          <strong> restricted</strong>: kato also re-checks its remote address, and runs a task it picks
          up by itself only with the Docker sandbox on (Settings → Sandbox &amp; permission bypass) and
          the security scanner blocking MEDIUM findings. Stored in{' '}
          <code>{meta.storagePath || '~/.kato/approved-repositories.json'}</code>.
        </p>
      </SettingsPanelHead>

      <SettingsPanelBody loading={loading} error={error} loadingMessage="Loading repositories…">
        <>
          {meta.rows.length === 0 ? (
            <p className="settings-drawer-message">
              No repositories found yet. Set the folder that holds your repositories in the
              Repositories tab.
            </p>
          ) : (
            <table className="settings-drawer-approvals-table">
              <thead>
                <tr>
                  <th>Approved</th>
                  <th>Repository</th>
                  <th>Mode</th>
                  <th>Source</th>
                </tr>
              </thead>
              <tbody>
                {meta.rows.map((row) => {
                  const e = rowState(row);
                  return (
                    <tr
                      key={row.repository_id}
                      className={row.remote_url_drift ? 'has-drift' : ''}
                    >
                      <td>
                        <label className="settings-drawer-approval-toggle">
                          <input
                            type="checkbox"
                            checked={e.approved}
                            onChange={(ev) =>
                              patchEdit(row.repository_id, { approved: ev.target.checked })
                            }
                          />
                        </label>
                      </td>
                      <td>
                        <div className="settings-drawer-approval-id">
                          {row.repository_id}
                        </div>
                        <div
                          className="settings-drawer-approval-url"
                          title={row.remote_url}
                        >
                          {row.remote_url || '(no remote)'}
                        </div>
                        {row.remote_url_drift && (
                          <div className="settings-drawer-approval-drift">
                            ⚠ discovered remote differs from approval:
                            {' '}
                            <code>{row.approved_remote_url}</code>
                            {' '}— toggle to re-approve the new URL.
                          </div>
                        )}
                      </td>
                      <td>
                        <select
                          className="settings-drawer-input is-compact"
                          value={e.mode}
                          onChange={(ev) =>
                            patchEdit(row.repository_id, { mode: ev.target.value })
                          }
                          disabled={!e.approved}
                        >
                          <option value={MODE_RESTRICTED}>restricted</option>
                          <option value={MODE_TRUSTED}>trusted</option>
                        </select>
                      </td>
                      <td>
                        <span className={`settings-drawer-source source-${row.source}`}>
                          {row.source}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}

          <SettingsActions
            onSecondary={refresh}
            secondaryLabel="Refresh"
            onSave={save}
            saving={saving}
            canSave={hasChanges}
            primaryLabel="Save changes"
          />

          {/* Which repos are candidates AT ALL. Lifted out of the General
              tab: the ignore list decides what appears in the table above,
              and the denylist is a hard refusal — both are approval
              decisions, so they belong on this tab. */}
          {showScope ? (
            <SchemaFieldGroup
              sectionId="general"
              panel="approvals"
              highlightKey={highlightKey}
              title="Repository scope"
              description={
                'Applied before the table above: ignored folders never reach '
                + 'auto-discovery, and denylisted ids are refused at boot even '
                + 'if approved. Both need a kato restart.'
              }
            />
          ) : null}
        </>
      </SettingsPanelBody>
    </div>
  );
}


// Like ``rowState`` but resolved against a snapshot — used inside
// the setEdits updater where the new edit needs the previous state
// without a fresh closure over ``meta.rows``.
function rowStateFor(currentEdits, rows, repoId) {
  const e = currentEdits[repoId];
  if (e) { return e; }
  const row = rows.find((r) => r.repository_id === repoId);
  return {
    approved: !!(row && row.approved),
    mode: modeOf(row),
  };
}

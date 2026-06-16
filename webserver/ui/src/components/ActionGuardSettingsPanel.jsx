import { useCallback, useEffect, useState } from 'react';
import { fetchActionGuardAudit } from '../api.js';
import { useSchemaSectionDraft } from '../hooks/useSchemaSectionDraft.js';
import PanelMessage from './settings/PanelMessage.jsx';
import SettingsPanelHead from './settings/SettingsPanelHead.jsx';
import SettingsActions from './settings/SettingsActions.jsx';

// Dedicated "Action Guard" tab — per-category posture (Block / Ask / Allow)
// for what the agent is allowed to do. Reads/writes the schema-backed
// ``KATO_ACTION_GUARD_*`` keys through /api/all-settings (same store as every
// other setting), but presents them as a security control panel rather than a
// generic field list: a master switch, a posture per risk category with its
// one-line description, and the locked floor categories shown read-only.
//
// Unlike most settings, Action Guard is LIVE — the engine re-reads posture per
// tool call, so a change applies on the next agent action with no restart.

const SECTION_ID = 'action_guard';
const ENABLED_KEY = 'KATO_ACTION_GUARD_ENABLED';
const KEY_PREFIX = 'KATO_ACTION_GUARD_';
const DECISIONS = ['block', 'ask', 'allow'];
// Categories the engine clamps to BLOCK no matter what (no legitimate use):
// shown read-only so the operator is never misled into thinking they unlock.
const ALWAYS_BLOCK = new Set(['remote_exec', 'sandbox_escape']);


function categoryOf(key) {
  return key.slice(KEY_PREFIX.length).toLowerCase();
}


export default function ActionGuardSettingsPanel() {
  const {
    loading, error, fields, draft, setField, savedAt, saveBarProps,
  } = useSchemaSectionDraft(SECTION_ID, {
    // Action Guard is live — no restart, unlike most schema settings.
    successMessage: 'Action Guard updated — applies to the next agent action.',
  });

  // Read-only "reviewing history": recent decisions from the audit log.
  const [audit, setAudit] = useState(null);
  const loadAudit = useCallback(() => {
    fetchActionGuardAudit().then((r) => { if (r.ok) { setAudit(r.body); } });
  }, []);
  useEffect(() => { loadAudit(); }, [loadAudit]);

  if (loading) {
    return <div className="settings-drawer-panel"><PanelMessage>Loading settings…</PanelMessage></div>;
  }
  if (error) {
    return <div className="settings-drawer-panel"><PanelMessage error>{error}</PanelMessage></div>;
  }

  const enabled = String(draft[ENABLED_KEY] ?? 'true').toLowerCase() !== 'false';
  const categoryFields = fields.filter((f) => f.key !== ENABLED_KEY);

  return (
    <div className="settings-drawer-panel">
      <SettingsPanelHead title="Action Guard">
        <p>
          Decide what the agent may do. <strong>Block</strong> refuses the
          action and tells the agent why, <strong>Ask</strong> prompts you,
          {' '}<strong>Allow</strong> lets it run. No-legitimate-use actions
          (reverse shells, fork bombs, <code>mkfs</code>, <code>dd</code>-to-device)
          and the locked categories below always block. Changes apply to the
          next agent action — no restart. Every decision is audited to{' '}
          <code>~/.kato/action-guard-audit.log</code>.
        </p>
      </SettingsPanelHead>

      <label className="settings-drawer-field is-toggle-row">
        <span className="settings-drawer-field-label">
          <span className="settings-drawer-field-name">Action Guard enabled</span>
          <span className="settings-drawer-field-hint">
            Master switch for content-aware blocking. The CLI denylist floor
            stays on regardless.
          </span>
        </span>
        <input
          type="checkbox"
          className="settings-drawer-toggle"
          checked={enabled}
          aria-label="Action Guard enabled"
          onChange={(e) => setField(ENABLED_KEY, e.target.checked ? 'true' : 'false')}
        />
      </label>

      <table className="settings-drawer-approvals-table">
        <thead>
          <tr><th>Risk category</th><th>Posture</th></tr>
        </thead>
        <tbody>
          {categoryFields.map((f) => {
            const category = categoryOf(f.key);
            const locked = ALWAYS_BLOCK.has(category);
            return (
              <tr key={f.key} data-field-key={f.key}>
                <td>
                  <div className="settings-drawer-approval-id">{f.label}</div>
                  {f.help && <div className="settings-drawer-field-hint">{f.help}</div>}
                </td>
                <td>
                  {locked ? (
                    <span
                      className="settings-drawer-source source-action_guard_secure_default"
                      title="No legitimate use — always blocked"
                    >
                      Always block
                    </span>
                  ) : (
                    <select
                      className="settings-drawer-input is-compact"
                      value={draft[f.key] ?? ''}
                      disabled={!enabled}
                      aria-label={`Posture for ${f.label}`}
                      onChange={(e) => setField(f.key, e.target.value)}
                    >
                      {DECISIONS.map((d) => <option key={d} value={d}>{d}</option>)}
                    </select>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <SettingsActions {...saveBarProps} />

      {savedAt && (
        <p className="settings-drawer-message">
          ✓ Saved — applies to the next agent action (no restart).
        </p>
      )}

      <div className="settings-action-guard-audit">
        <SettingsPanelHead title="Recent decisions">
          <p>
            What the agent tried and how the guard ruled — newest first, from{' '}
            <code>~/.kato/action-guard-audit.log</code>.
          </p>
        </SettingsPanelHead>
        <button type="button" className="secondary" onClick={loadAudit}>
          Refresh
        </button>
        {audit && !audit.ok && (
          <p className="settings-drawer-message">
            ⚠ Audit integrity check FAILED at entry {audit.first_bad_index} —
            the log may have been edited.
          </p>
        )}
        {renderAuditRows(audit)}
      </div>
    </div>
  );
}


function formatTs(iso) {
  if (!iso) { return ''; }
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString();
}


// Built outside JSX (AGENTS.md "no logic inside JSX"). Shows a loading note
// until the first fetch resolves, an empty note when nothing is logged, else
// the decisions table.
function renderAuditRows(audit) {
  if (!audit) {
    return <p className="settings-drawer-message">Loading history…</p>;
  }
  const entries = Array.isArray(audit.entries) ? audit.entries : [];
  if (entries.length === 0) {
    return <p className="settings-drawer-message">No decisions recorded yet.</p>;
  }
  return (
    <table className="settings-drawer-approvals-table">
      <thead>
        <tr>
          <th>When</th><th>Task</th><th>Category</th>
          <th>Decision</th><th>Command</th><th>By</th>
        </tr>
      </thead>
      <tbody>
        {entries.map((e, i) => (
          <tr key={`${e.timestamp || ''}-${i}`}>
            <td>{formatTs(e.timestamp)}</td>
            <td>{e.task_id || ''}</td>
            <td>{String(e.category || '').replace(/_/g, ' ')}</td>
            <td>{e.decision || ''}</td>
            <td><code className="settings-perm-command">{e.command_preview || ''}</code></td>
            <td>{e.answered_by || ''}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

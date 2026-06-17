import { useState } from 'react';
import { useAgentVersion, refreshAgentVersion } from '../hooks/useAgentVersion.js';
import { upgradeAgentCli } from '../api.js';
import { toast } from '../stores/toastStore.js';
import AgentUpgradeModal from './AgentUpgradeModal.jsx';

// Always-visible banner warning that the CONFIGURED agent CLI is out of date
// (or missing). Mirrors SafetyBanner: self-contained, renders nothing in the
// healthy case. When in-app upgrade is enabled (claude + npm, non-Docker) it
// also offers an "Upgrade" action gated behind an explicit per-use confirm.
// On success it re-probes so the banner + the ultracode toggle update live —
// no page reload.
export default function AgentVersionBanner() {
  const info = useAgentVersion();
  const [phase, setPhase] = useState('idle');  // idle | confirming | running
  const message = bannerMessage(info);
  if (!message) { return null; }

  const url = String(info?.download_url || '').trim();
  const command = String(info?.upgrade_command || '');

  async function runUpgrade() {
    setPhase('running');
    const result = await upgradeAgentCli();
    const body = (result && result.body) || {};
    if (body.ok) {
      toast.show({
        kind: 'success', title: 'Agent CLI upgraded',
        message: body.message || 'Upgraded.', durationMs: 9000,
      });
      // Live update — banner clears + the ultracode toggle (re)appears with no
      // reload. New agent runs already use the new binary.
      await refreshAgentVersion();
      setPhase('idle');
    } else {
      toast.show({
        kind: 'error', title: 'Upgrade failed',
        message: body.message || (result && result.error) || 'Upgrade failed.',
        durationMs: 11000,
      });
      setPhase('idle');
    }
  }

  // "not found" is genuinely blocking (the backend can't run) → assertive
  // warning. "out of date" is advisory → calm, polite status (not a red alarm).
  const severity = info?.found === false ? 'warn' : 'info';
  const role = severity === 'warn' ? 'alert' : 'status';

  return (
    <>
      <div
        className={`kato-version-banner kato-version-banner--${severity}`}
        role={role}
        aria-live="polite"
      >
        <span className="kato-version-banner__icon" aria-hidden="true">
          {severity === 'warn' ? '!' : '↑'}
        </span>
        {renderText(message, url)}
        {info?.can_upgrade ? (
          <button
            type="button"
            className="primary kato-version-banner__upgrade"
            onClick={() => setPhase('confirming')}
          >
            Upgrade now
          </button>
        ) : renderBlockedReason(info)}
      </div>
      {/* The confirm is a popup (not crammed into the banner row). */}
      {info?.can_upgrade && (phase === 'confirming' || phase === 'running') && (
        <AgentUpgradeModal
          command={command}
          running={phase === 'running'}
          onConfirm={runUpgrade}
          onCancel={() => setPhase('idle')}
        />
      )}
    </>
  );
}

// Plain message text followed by a SINGLE link to the install/upgrade page.
// (The whole sentence used to be one <a>, whose underline split around the
// `claude` code chip and read as two links — keep the link to one phrase.)
function renderText(message, url) {
  return (
    <span className="kato-version-banner__text">
      {message}
      {url ? (
        <>
          {' '}
          <a
            className="kato-version-banner__link"
            href={url}
            target="_blank"
            rel="noopener noreferrer"
          >
            open the download page →
          </a>
        </>
      ) : null}
    </span>
  );
}

// When there IS an update but one-click upgrade isn't available (Docker image,
// codex backend, or hard-disabled), say why instead of going silent.
function renderBlockedReason(info) {
  const reason = String(info?.upgrade_blocked_reason || '').trim();
  if (!reason) { return null; }
  return <span className="kato-version-banner__note">{reason}</span>;
}

// Returns the banner text, or null when nothing's wrong (loading, OpenHands,
// or up to date).
function bannerMessage(info) {
  if (!info || !info.backend || info.backend === 'openhands' || info.backend === 'unknown') {
    return null;
  }
  const name = String(info.backend).toUpperCase();
  if (info.found === false) {
    return (
      <>
        <strong>{name} CLI not found on PATH</strong>
        {' '}(<code>{info.binary}</code>). The configured agent backend can't
        run until its CLI is installed and on PATH.
      </>
    );
  }
  if (info.up_to_date === false) {
    const ver = info.version || info.version_raw || 'unknown';
    const min = info.recommended_min ? ` (recommended ≥ ${info.recommended_min})` : '';
    return (
      <>
        <strong>{name} CLI update available</strong> — you're on {ver}{min}.
        {' '}Upgrade the <code>{info.binary}</code> CLI on the kato host for the
        latest fixes{info.backend === 'claude' ? ', subagents, and workflows/ultracode' : ''}.
      </>
    );
  }
  return null;
}

import { useState } from 'react';
import { useAgentVersion, refreshAgentVersion } from '../hooks/useAgentVersion.js';
import { upgradeAgentCli } from '../api.js';
import { toast } from '../stores/toastStore.js';

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

  return (
    <div className="kato-safety-banner" role="alert" aria-live="polite">
      <span className="kato-safety-banner__icon" aria-hidden="true">!</span>
      {renderText(message, url)}
      {info?.can_upgrade && renderUpgrade(phase, command, setPhase, runUpgrade)}
    </div>
  );
}

// The message, as a link to the official install/upgrade page when known.
function renderText(message, url) {
  if (url) {
    return (
      <a
        className="kato-safety-banner__text kato-safety-banner__link"
        href={url}
        target="_blank"
        rel="noopener noreferrer"
      >
        {message} <span aria-hidden="true">→ open the download page</span>
      </a>
    );
  }
  return <span className="kato-safety-banner__text">{message}</span>;
}

// The in-app upgrade action: "Upgrade now" → an explicit confirm showing the
// exact command (the operator's approval) → run. Built outside JSX.
function renderUpgrade(phase, command, setPhase, runUpgrade) {
  if (phase === 'running') {
    return <span className="kato-safety-banner__upgrade">Upgrading…</span>;
  }
  if (phase === 'confirming') {
    return (
      <span className="kato-safety-banner__upgrade">
        Run <code>{command}</code> on the host?{' '}
        <button type="button" className="primary" onClick={runUpgrade}>
          Confirm upgrade
        </button>{' '}
        <button type="button" className="secondary" onClick={() => setPhase('idle')}>
          Cancel
        </button>
      </span>
    );
  }
  return (
    <button
      type="button"
      className="primary kato-safety-banner__upgrade"
      onClick={() => setPhase('confirming')}
    >
      Upgrade now
    </button>
  );
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
        <strong>{name} CLI {ver} is out of date{min}.</strong>
        {' '}Upgrade the <code>{info.binary}</code> CLI on the kato host for the
        latest fixes{info.backend === 'claude' ? ', subagents, and workflows/ultracode' : ''}.
      </>
    );
  }
  return null;
}

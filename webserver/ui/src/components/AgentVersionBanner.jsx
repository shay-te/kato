import { useState } from 'react';
import { useAgentVersion } from '../hooks/useAgentVersion.js';
import { useAgentUpgrade } from '../hooks/useAgentUpgrade.js';
import AgentUpgradeModal from './AgentUpgradeModal.jsx';

// Always-visible banner telling the operator the CONFIGURED agent CLI is
// behind the published release, below the recommended minimum, or missing.
// Mirrors SafetyBanner: self-contained, renders nothing in the healthy case.
// When in-app upgrade is available it offers an "Upgrade" action gated behind
// an explicit per-use confirm, then shows a live progress bar. The version
// re-probe that clears the banner (and re-enables the ultracode toggle) is
// driven by useAgentUpgrade — no page reload.
export default function AgentVersionBanner() {
  const info = useAgentVersion();
  const { progress, start, dismiss } = useAgentUpgrade();
  const [confirming, setConfirming] = useState(false);
  const message = bannerMessage(info);
  const upgrading = Boolean(progress);
  // An upgrade started from an earlier render (or another tab, or before a
  // reload) must keep its modal even once the banner text clears.
  if (!message && !upgrading) { return null; }

  const url = String(info?.download_url || '').trim();
  const command = String(info?.upgrade_command || '');

  function closeModal() {
    dismiss();
    setConfirming(false);
  }

  // "not found" is genuinely blocking (the backend can't run) → assertive
  // warning. "out of date" is advisory → calm, polite status (not a red alarm).
  const severity = info?.found === false ? 'warn' : 'info';
  const role = severity === 'warn' ? 'alert' : 'status';

  return (
    <>
      {message && (
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
              onClick={() => setConfirming(true)}
            >
              Upgrade now
            </button>
          ) : renderBlockedReason(info)}
        </div>
      )}
      {/* The confirm + progress live in a popup (not crammed into the banner
          row). It stays open through the whole run and the outcome. */}
      {(confirming || upgrading) && (
        <AgentUpgradeModal
          command={command}
          progress={progress}
          onConfirm={start}
          onCancel={closeModal}
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
  if (info.update_available || info.up_to_date === false) {
    const ver = info.version || info.version_raw || 'unknown';
    // Prefer the CONCRETE published version over the recommended floor: "on
    // 2.1.179, latest is 2.1.222" is actionable, "recommended ≥ 2.1.160" while
    // sitting on 2.1.179 reads as though nothing is wrong.
    const target = info.latest_version
      ? ` — latest is ${info.latest_version}`
      : (info.recommended_min ? ` (recommended ≥ ${info.recommended_min})` : '');
    return (
      <>
        <strong>{name} CLI update available</strong> — you're on {ver}{target}.
        {' '}Upgrade the <code>{info.binary}</code> CLI on the kato host for the
        latest fixes{info.backend === 'claude' ? ', subagents, and workflows/ultracode' : ''}.
      </>
    );
  }
  return null;
}

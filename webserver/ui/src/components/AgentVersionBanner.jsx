import { useAgentVersion } from '../hooks/useAgentVersion.js';

// Always-visible banner warning that the CONFIGURED agent CLI is out of date
// (or missing). Mirrors SafetyBanner: self-contained, renders nothing in the
// healthy case. OpenHands (no local CLI) and up-to-date CLIs show nothing.
export default function AgentVersionBanner() {
  const info = useAgentVersion();
  const message = bannerMessage(info);
  if (!message) { return null; }
  return (
    <div className="kato-safety-banner" role="alert" aria-live="polite">
      <span className="kato-safety-banner__icon" aria-hidden="true">!</span>
      <span className="kato-safety-banner__text">{message}</span>
    </div>
  );
}

// Built outside JSX. Returns the banner text, or null when nothing's wrong
// (still loading, OpenHands, or up to date).
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

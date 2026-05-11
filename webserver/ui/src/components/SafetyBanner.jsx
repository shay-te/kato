export default function SafetyBanner({ state }) {
  if (!state || !state.bypass_permissions) {
    return null;
  }
  return (
    <div className="kato-safety-banner" role="alert" aria-live="polite">
      <span className="kato-safety-banner__icon" aria-hidden="true">!</span>
      <span className="kato-safety-banner__text">
        <strong>KATO_CLAUDE_BYPASS_PERMISSIONS=true.</strong>
        {' '}
        The agent is running every tool without asking — Bash, Edit, Write,
        anything Claude exposes. Per-tool permission prompts are disabled.
        Operator acknowledged the double-confirm at startup.
        {' '}
        See SECURITY.md.
      </span>
    </div>
  );
}

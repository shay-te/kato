import { backendLabel } from './AgentBackendChip.jsx';

// Shown IN PLACE of the chat when the selected agent's CLI isn't usable on
// this host. Both agent tabs always exist — hiding a tab is how an operator
// never discovers kato can run that backend at all — so selecting an
// unconfigured one has to explain itself rather than open a dead chat box.
//
// The text is the transport's OWN ``validate_connection()`` message, passed
// straight through: it already carries the exact install and login commands
// (``npm install -g @openai/codex``, ``codex login``), so this renders one
// source of truth instead of a second copy that drifts from the validator.

export default function AgentBackendSetup({
  backend, error, onRecheck, rechecking = false, wired = true,
}) {
  const label = backendLabel(backend) || backend;
  const detail = String(error || '').trim();
  // Two different problems wear the same tab. A CLI that answers but has no
  // session manager behind it is not a missing install — telling that
  // operator to re-check the binary path sends them hunting for a fault
  // that isn't there; they just need to restart kato.
  const needsRestart = !wired && !detail;

  return (
    <section className="agent-backend-setup" aria-labelledby="agent-backend-setup-title">
      <h3 id="agent-backend-setup-title" className="agent-backend-setup-title">
        {label} isn&apos;t set up on this host
      </h3>
      <p className="agent-backend-setup-lead">
        This tab will become a normal chat as soon as the {label} CLI is
        installed and reachable. Your other agent tabs are unaffected.
      </p>

      {needsRestart ? (
        <p className="agent-backend-setup-detail is-empty">
          The {label} CLI is installed and answering — kato just hasn&apos;t
          wired it into this session yet. Restart kato to pick it up.
        </p>
      ) : detail ? (
        // Pre-formatted: the validator's message is deliberately laid out
        // with indented shell commands on their own lines.
        <pre className="agent-backend-setup-detail">{detail}</pre>
      ) : (
        <p className="agent-backend-setup-detail is-empty">
          No detail was reported — check that the binary path in
          Settings → {label} agent points at the CLI.
        </p>
      )}

      <div className="agent-backend-setup-actions">
        <button
          type="button"
          className="agent-backend-setup-recheck"
          onClick={onRecheck}
          disabled={rechecking}
        >
          {rechecking ? 'Checking…' : 'Check again'}
        </button>
        <span className="agent-backend-setup-hint">
          Already installed? The result is cached for a minute — “Check
          again” re-runs it now.
        </span>
      </div>
    </section>
  );
}

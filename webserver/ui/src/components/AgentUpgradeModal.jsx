import { useEffect, useRef } from 'react';
import { useEscapeKey } from '../hooks/useEscapeKey.js';
import DialogShell from './DialogShell.jsx';

/**
 * Confirm popup for the in-app agent-CLI upgrade.
 *
 * The approval used to expand inline inside the version banner (the top
 * toolbar). It's now a real centered modal: clearer, and it can't be
 * missed in the banner's cramped row. Shows the EXACT command that will
 * run on the host (the operator's approval) and the fact that chats /
 * sessions / login survive. While the upgrade runs, both buttons are
 * disabled and backdrop / Esc dismissal is suppressed so it can't be
 * cancelled mid-flight.
 */
export default function AgentUpgradeModal({ command, running, onConfirm, onCancel }) {
  const cancelRef = useRef(null);

  useEffect(() => {
    cancelRef.current?.focus();
  }, []);

  // Esc cancels — but not while the upgrade is mid-flight.
  useEscapeKey(() => { if (!running) { onCancel(); } });

  return (
    <DialogShell
      id="agent-upgrade-modal"
      ariaLabelledBy="agent-upgrade-title"
      title="Upgrade the agent CLI?"
      subtitle="Runs once on the kato host"
      subtitleId="agent-upgrade-subtitle"
      onClose={() => { if (!running) { onCancel(); } }}
      backdropClose={!running}
    >
      <p className="agent-upgrade-lead">
        This installs the latest Claude CLI by running the command below on
        the kato host. Your chats, sessions, and login are preserved — only
        the binary is replaced.
      </p>
      <pre className="agent-upgrade-command"><code>{command}</code></pre>

      <div className="modal-actions">
        <button
          type="button"
          className="secondary"
          ref={cancelRef}
          onClick={onCancel}
          disabled={running}
        >
          Cancel
        </button>
        <button
          type="button"
          className="primary"
          onClick={onConfirm}
          disabled={running}
        >
          {running ? 'Upgrading…' : 'Confirm upgrade'}
        </button>
      </div>
    </DialogShell>
  );
}

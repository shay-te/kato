import { useEffect, useRef } from 'react';
import { useEscapeKey } from '../hooks/useEscapeKey.js';
import DialogShell from './DialogShell.jsx';

/**
 * Confirm + progress popup for the in-app agent-CLI upgrade.
 *
 * Three states in one dialog:
 *   confirm  — shows the EXACT command that will run on the host (the
 *              operator's approval) and that chats / sessions / login survive;
 *   running  — a determinate progress bar, the current step, and the live
 *              command output, so a multi-minute install isn't a frozen
 *              button. Backdrop / Esc dismissal is suppressed mid-flight;
 *   finished — the outcome (with the version change) and the output to read
 *              back on failure.
 *
 * The bar tracks milestones the host actually observed in the command output;
 * it only reaches 100% once the new binary has been re-probed and reported its
 * version, so "complete" is never a guess.
 */
export default function AgentUpgradeModal({ command, progress, onConfirm, onCancel }) {
  const cancelRef = useRef(null);
  const logRef = useRef(null);
  const state = progress?.state || 'confirm';
  const running = state === 'running';
  const finished = state === 'done' || state === 'error';

  useEffect(() => {
    cancelRef.current?.focus();
  }, []);

  // Follow the output as it streams.
  useEffect(() => {
    const node = logRef.current;
    if (node) { node.scrollTop = node.scrollHeight; }
  }, [progress?.lines]);

  // Esc closes — but not while the upgrade is mid-flight.
  useEscapeKey(() => { if (!running) { onCancel(); } });

  const shown = String(progress?.command || command || '');

  return (
    <DialogShell
      id="agent-upgrade-modal"
      ariaLabelledBy="agent-upgrade-title"
      title={finished ? upgradeTitle(progress) : 'Upgrade the agent CLI?'}
      subtitle="Runs once on the kato host"
      subtitleId="agent-upgrade-subtitle"
      onClose={() => { if (!running) { onCancel(); } }}
      backdropClose={!running}
    >
      {!finished && (
        <p className="agent-upgrade-lead">
          This installs the latest CLI by running the command below on the kato
          host. Your chats, sessions, and login are preserved — only the binary
          is replaced.
        </p>
      )}
      {shown ? <pre className="agent-upgrade-command"><code>{shown}</code></pre> : null}

      {running && <UpgradeProgress progress={progress} />}
      {finished && <UpgradeOutcome progress={progress} />}
      {(running || finished) && <UpgradeLog progress={progress} logRef={logRef} />}

      <div className="modal-actions">
        {finished ? (
          <button type="button" className="primary" onClick={onCancel}>Close</button>
        ) : (
          <>
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
          </>
        )}
      </div>
    </DialogShell>
  );
}

// Determinate bar + the step the host is on. ``aria-valuenow`` keeps the
// percentage available to screen readers, which a bare styled div would not.
function UpgradeProgress({ progress }) {
  const percent = clampPercent(progress?.percent);
  return (
    <div className="agent-upgrade-progress">
      <div
        className="agent-upgrade-progress__track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-label="Agent CLI upgrade progress"
      >
        <div
          className="agent-upgrade-progress__fill"
          style={{ width: `${percent}%` }}
        />
      </div>
      <div className="agent-upgrade-progress__meta">
        <span className="agent-upgrade-progress__step">
          {progress?.step || 'Working…'}
        </span>
        <span className="agent-upgrade-progress__percent">{percent}%</span>
      </div>
    </div>
  );
}

function UpgradeOutcome({ progress }) {
  const ok = progress?.state === 'done';
  return (
    <p
      className={`agent-upgrade-outcome agent-upgrade-outcome--${ok ? 'ok' : 'error'}`}
      role="status"
    >
      {progress?.message || (ok ? 'Upgraded.' : 'Upgrade failed.')}
    </p>
  );
}

// The host's own command output. Shown while running (so a long install is
// visibly alive) and kept after a failure (npm's error text is the only way to
// tell EACCES from a network problem).
function UpgradeLog({ progress, logRef }) {
  const lines = progress?.lines || [];
  if (!lines.length) { return null; }
  return (
    <pre className="agent-upgrade-log" ref={logRef} aria-label="Upgrade output">
      {lines.join('\n')}
    </pre>
  );
}

function upgradeTitle(progress) {
  return progress?.state === 'done' ? 'Agent CLI upgraded' : 'Upgrade failed';
}

function clampPercent(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) { return 0; }
  return Math.max(0, Math.min(100, Math.round(number)));
}

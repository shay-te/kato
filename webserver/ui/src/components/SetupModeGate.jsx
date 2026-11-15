import { useState } from 'react';
import QuickStart from './QuickStart.jsx';
import SetupWizard from './SetupWizard.jsx';

// Full-screen gate shown ONLY when kato booted unconfigured
// (``status.setup_mode``) on a FIRST run. It blocks the normal task UI — there
// are no tasks to show yet — and hands the operator quick start (or the
// ticket-system wizard, one link away) so kato gets running from the browser.
// It disappears on its own once the config-status poll reports the process is
// no longer in setup mode.
//
// A kato that is set up and only cannot CONNECT (a refused token, a host that
// is down) is NOT gated: the operator gets kato's own screen, the problem in a
// banner (ConnectionProblemsBanner) and the fix — with "Test connection" — in
// Settings. It used to drop them into the full wizard instead ("the previous
// quick start was better than this wizard; I want the verification inside the
// settings").
//
// ``hidden`` (drawer open) hides the overlay WITHOUT unmounting, so the
// Settings drawer paints on top while the wizard keeps its typed state.
export default function SetupModeGate({ status, hidden = false, onRefreshStatus, onOpenFullSettings }) {
  const [wizard, setWizard] = useState(false);
  if (!status || !status.setup_mode || status.setup_kind === 'cannot_connect') {
    return null;
  }
  return (
    <div
      className={'setup-gate' + (hidden ? ' is-hidden' : '')}
      role="dialog"
      aria-modal="true"
      aria-labelledby="setup-gate-title"
    >
      <div className="setup-gate-panel">
        <header className="setup-gate-head">
          <span className="setup-gate-badge">Setup required</span>
          <h2 id="setup-gate-title" className="setup-gate-title">Welcome to Kato</h2>
          <p className="setup-gate-sub">
            {wizard
              ? 'Connect your ticket system, then kato picks up the tasks assigned to you.'
              : 'Pick your agent and your code folder — kato checks and installs the rest. No terminal needed.'}
          </p>
          {Boolean(status.setup_error) && (
            <div className="setup-gate-error" role="alert">
              <strong>Start failed:</strong>
              <pre>{status.setup_error}</pre>
              Fix the values and kato retries automatically.
            </div>
          )}
        </header>
        {wizard ? (
          <>
            <button type="button" className="quick-start-link setup-gate-back" onClick={() => setWizard(false)}>
              ← Back to quick start
            </button>
            <SetupWizard
              status={status}
              onRefreshStatus={onRefreshStatus}
              onOpenFullSettings={onOpenFullSettings}
            />
          </>
        ) : (
          <QuickStart onStarted={onRefreshStatus} onUseWizard={() => setWizard(true)} />
        )}
      </div>
    </div>
  );
}

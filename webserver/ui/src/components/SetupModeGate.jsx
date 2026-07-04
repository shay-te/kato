import SetupWizard from './SetupWizard.jsx';

// Full-screen gate shown ONLY when kato booted unconfigured
// (``status.setup_mode``). It blocks the normal task UI — there are no tasks
// to show yet — and hands the operator the first-run wizard so they can get
// kato running entirely from the browser. It disappears on its own once the
// config-status poll reports the process is no longer in setup mode.
//
// ``hidden`` (drawer open) hides the overlay WITHOUT unmounting, so the
// Settings drawer paints on top while the wizard keeps its typed state.
export default function SetupModeGate({ status, hidden = false, onRefreshStatus, onOpenFullSettings }) {
  if (!status || !status.setup_mode) {
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
            Kato isn't configured yet — new here? This quick setup gets it
            running. No terminal needed.
          </p>
          {Boolean(status.setup_error) && (
            <p className="setup-gate-error" role="alert">
              <strong>Start failed:</strong> {status.setup_error}
              {' '}Fix the values and kato retries automatically.
            </p>
          )}
        </header>
        <SetupWizard
          status={status}
          onRefreshStatus={onRefreshStatus}
          onOpenFullSettings={onOpenFullSettings}
        />
      </div>
    </div>
  );
}

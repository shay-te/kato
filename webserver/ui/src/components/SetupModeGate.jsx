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
  // Set up already, and a service it needs could not be reached (a token
  // that expired, a host that is down): not a first run, and not "welcome".
  const cannotConnect = status.setup_kind === 'cannot_connect';
  return (
    <div
      className={'setup-gate' + (hidden ? ' is-hidden' : '')}
      role="dialog"
      aria-modal="true"
      aria-labelledby="setup-gate-title"
    >
      <div className="setup-gate-panel">
        <header className="setup-gate-head">
          <span className="setup-gate-badge">{cannotConnect ? 'Connection problem' : 'Setup required'}</span>
          <h2 id="setup-gate-title" className="setup-gate-title">
            {cannotConnect ? 'Kato can’t reach a service it needs' : 'Welcome to Kato'}
          </h2>
          <p className="setup-gate-sub">
            {cannotConnect
              ? 'Your settings are saved — kato keeps trying by itself. Fix what the error names, '
                + 'below or in Settings.'
              : 'Kato isn\'t configured yet — new here? This quick setup gets it running. No terminal needed.'}
          </p>
          {Boolean(status.setup_error) && (
            <div className="setup-gate-error" role="alert">
              <strong>{cannotConnect ? 'Could not connect:' : 'Start failed:'}</strong>
              <pre>{status.setup_error}</pre>
              Fix the values and kato retries automatically.
            </div>
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

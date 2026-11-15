// What kato could not reach on its last try — the ticket system, a git host,
// your repositories, the agent — in the service's own words, until it answers
// again. ONE source: the config status payload's ``connection_problems``
// (connection_health.py, polled with it every 5s; no request of its own), fed
// by kato's startup check, its scans, and the apply-and-recheck Settings calls
// after a good test or a save — so a test, a save and this banner never
// disagree. Never read ``setup_error`` here: that was a second source.
//
// Each problem opens the Settings tab where its connection is set and tested.
export const SETTINGS_TAB_FOR_KIND = Object.freeze({
  tracker: 'task-provider',
  git_host: 'git-provider',
  repositories: 'repositories',
  settings: '',  // Settings, on its first tab
});

export function connectionProblemTitle(problem) {
  const tries = Number(problem?.failures) || 0;
  const label = problem?.label || 'a service';
  return tries > 1 ? `Kato can't reach ${label} (${tries} tries in a row)` : `Kato can't reach ${label}`;
}

export default function ConnectionProblemsBanner({ problems, onOpenSettings }) {
  const list = Array.isArray(problems) ? problems : [];
  if (!list.length) { return null; }
  return (
    <>
      {list.map((problem) => (
        <div key={problem.key} className="kato-version-banner kato-version-banner--warn connection-problem-banner" role="alert">
          <span className="kato-version-banner__icon" aria-hidden="true">!</span>
          <div className="connection-problem-banner__body">
            <strong>{connectionProblemTitle(problem)}</strong>
            {problem.note && <span>{problem.note}</span>}
            <pre className="connection-problem-banner__error">{problem.error}</pre>
          </div>
          {problem.kind in SETTINGS_TAB_FOR_KIND && onOpenSettings && (
            <button
              type="button"
              className="primary"
              onClick={() => onOpenSettings(SETTINGS_TAB_FOR_KIND[problem.kind])}
            >
              Check the settings
            </button>
          )}
        </div>
      ))}
    </>
  );
}

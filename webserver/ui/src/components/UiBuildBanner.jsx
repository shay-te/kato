import { useUiBuildReload } from '../hooks/useUiBuildReload.js';

// "A newer UI is ready" — shown only while the operator is actually using the
// window, which is the one case the reload is NOT automatic (see
// useUiBuildReload). Same banner family as the agent-CLI update notice.
export default function UiBuildBanner() {
  const { stale, reload } = useUiBuildReload();
  if (!stale) { return null; }
  return (
    <div className="kato-version-banner kato-version-banner--info" role="status" aria-live="polite">
      <span className="kato-version-banner__icon" aria-hidden="true">↑</span>
      <span className="kato-version-banner__text">
        <strong>A newer version of this page is ready.</strong>
        {' '}It loads by itself the next time you switch away from this window.
      </span>
      <button
        type="button"
        className="primary kato-version-banner__upgrade"
        onClick={reload}
      >
        Reload now
      </button>
    </div>
  );
}

import { useEffect } from 'react';
import { useCredentialSources } from '../../hooks/useCredentialSources.js';

// "Don't make me mint an API token" — the operator-facing half of the
// credential-source ladder (kato_core_lib/helpers/credential_sources.py).
//
// Anyone running an autonomous coding agent has already authenticated to
// their code host at least once, so kato looks before it asks: the gh/glab
// CLI login, git's own credential helper, a conventional env var. Picking one
// stores only WHICH source to use — the token is resolved server-side at boot
// and never written to settings.json or sent to this browser.
//
// Pasting stays a first-class choice, not a punishment: the paste option is
// always in the list, and a provider with nothing to discover (Jira, YouTrack)
// renders nothing at all so the form looks exactly as it did before.
export const PASTED_SOURCE = 'pasted';

export default function CredentialSourcePicker({
  provider, value, onChange, providerLabel = '',
}) {
  const { sources, loading } = useCredentialSources(provider);
  // Adopt the first (cheapest) source as the default the moment discovery
  // lands. Reporting it UP rather than only rendering it selected is what
  // lets the form drop its "paste a token" requirement — a picker that
  // looked chosen while the parent still demanded a token was the first
  // version's bug.
  const first = sources[0]?.id || '';
  useEffect(() => {
    if (first && !value) { onChange(first); }
  }, [first, value, onChange]);
  if (loading || sources.length === 0) { return null; }
  const label = providerLabel || provider;
  const selected = value || first;
  return (
    <section className="credential-source">
      <p className="credential-source__lead">
        Kato found a {label} login already on this machine — use it and there
        is no token to create.
      </p>
      <div
        className="credential-source__options"
        role="radiogroup"
        aria-label={`${label} credential`}
      >
        {sources.map((source) => (
          <button
            key={source.id}
            type="button"
            role="radio"
            aria-checked={selected === source.id}
            className={
              'credential-source__option'
              + (selected === source.id ? ' is-selected' : '')
            }
            onClick={() => onChange(source.id)}
          >
            <span className="credential-source__option-label">
              {source.label}
              {source.account && (
                <span className="credential-source__account">{source.account}</span>
              )}
            </span>
            <span className="credential-source__option-detail">{source.detail}</span>
          </button>
        ))}
        <button
          type="button"
          role="radio"
          aria-checked={selected === PASTED_SOURCE}
          className={
            'credential-source__option'
            + (selected === PASTED_SOURCE ? ' is-selected' : '')
          }
          onClick={() => onChange(PASTED_SOURCE)}
        >
          <span className="credential-source__option-label">
            Paste a token instead
          </span>
          <span className="credential-source__option-detail">
            Use a token you create yourself — the steps are below.
          </span>
        </button>
      </div>
    </section>
  );
}

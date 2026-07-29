import { useEffect, useState } from 'react';
import { fetchCredentialSources } from '../api.js';

// Credentials for `provider` that ALREADY exist on this machine — the gh/glab
// CLI login, git's credential helper, a conventional env var. Shared by the
// first-run wizard and the Settings credentials panels so "connect without
// pasting a token" behaves identically in both.
//
// Probing shells out on the server (gh, git), so this fires once per provider
// selection, never on a timer. A failed probe resolves to an empty list: the
// paste form is always the fallback and must never be blocked by a wedged CLI.
//
// Returns `{ sources, loading }` — `sources` is [] until the first response.
export function useCredentialSources(provider) {
  const [state, setState] = useState({ sources: [], loading: Boolean(provider) });

  useEffect(() => {
    if (!provider) {
      setState({ sources: [], loading: false });
      return undefined;
    }
    let cancelled = false;
    setState({ sources: [], loading: true });
    fetchCredentialSources(provider)
      .then((result) => {
        if (cancelled) { return; }
        const sources = (result?.ok && Array.isArray(result.body?.sources))
          ? result.body.sources
          : [];
        setState({ sources, loading: false });
      })
      .catch(() => {
        if (!cancelled) { setState({ sources: [], loading: false }); }
      });
    return () => { cancelled = true; };
  }, [provider]);

  return state;
}

export default useCredentialSources;

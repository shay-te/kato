// The file-search narrowing toggles (VS Code's find-widget "Aa" / whole-word
// pair), remembered per browser.
//
// Client-side only: which way an operator likes to search is a preference of
// theirs, not state of the task — the same reasoning as the composer's
// steer-vs-send pref. Persisted so a reload doesn't quietly widen the search
// back out under someone who narrowed it on purpose.

const KEY = 'kato.fileSearch.v1';

const DEFAULTS = { matchCase: false, exact: false };

export function readFileSearchPrefs() {
  try {
    const stored = JSON.parse(window.localStorage.getItem(KEY) || 'null');
    if (!stored || typeof stored !== 'object') { return { ...DEFAULTS }; }
    return { matchCase: !!stored.matchCase, exact: !!stored.exact };
  } catch {
    // A private window, blocked site data, or a corrupt value — the search
    // must still work, just unremembered.
    return { ...DEFAULTS };
  }
}

export function writeFileSearchPrefs(prefs) {
  const next = { matchCase: !!prefs?.matchCase, exact: !!prefs?.exact };
  try {
    window.localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // Non-fatal: the toggle still applies for this session.
  }
  return next;
}

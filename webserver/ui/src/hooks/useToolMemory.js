import { useCallback, useEffect, useRef, useState } from 'react';

// Where the operator's "always allow / always deny" choices live in
// localStorage. Keyed by tool name (e.g. ``Bash``, ``Edit``, ``Write``)
// so the same approval covers every invocation of that tool — same
// granularity Claude Code's own "remember" checkbox uses. Persisting
// across kato restarts is the whole point: re-prompting for git after
// every server restart was the operator pain that drove this. If a
// future need for finer-grained scoping appears (per-task,
// per-command-pattern), bump the key suffix and migrate.
const STORAGE_KEY = 'kato.toolDecisions.v1';


// Exported for unit tests so the persistence layer can be verified
// independent of React's hook plumbing. Underscore-prefixed names
// signal "test surface, not part of the public API"; consumers
// should still go through ``useToolMemory``.
export const _readPersistedForTest = readPersisted;
export const _writePersistedForTest = writePersisted;

function readPersisted() {
  if (typeof window === 'undefined' || !window.localStorage) {
    return {};
  }
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) { return {}; }
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') { return {}; }
    return parsed;
  } catch (_) {
    return {};
  }
}


function writePersisted(decisions) {
  if (typeof window === 'undefined' || !window.localStorage) { return; }
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(decisions));
  } catch (_) { /* quota / private-mode failures are non-fatal */ }
}


export function useToolMemory() {
  // ``decisionsRef`` holds the live source of truth so ``recall`` can
  // be a synchronous lookup; ``version`` bumps on every mutation so
  // consumers that depend on the recall result re-render. The recall
  // callback returns the same value across renders for the same
  // tool, so React's identity-based memoization stays sane.
  const decisionsRef = useRef(readPersisted());
  const [version, setVersion] = useState(0);

  // Cross-tab sync: another browser tab persisting a decision should
  // immediately affect this tab too — otherwise the operator clicks
  // "remember" once and the *other* open tab still shows the prompt.
  useEffect(() => {
    function onStorage(event) {
      if (event.key !== STORAGE_KEY) { return; }
      decisionsRef.current = readPersisted();
      setVersion((n) => n + 1);
    }
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  const remember = useCallback((toolName, allow) => {
    if (!toolName) { return; }
    const next = { ...decisionsRef.current, [toolName]: allow ? 'allow' : 'deny' };
    decisionsRef.current = next;
    writePersisted(next);
    setVersion((n) => n + 1);
  }, []);

  const recall = useCallback((toolName) => {
    if (!toolName) { return null; }
    return decisionsRef.current[toolName] || null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version]);

  const forget = useCallback((toolName) => {
    if (!toolName) {
      decisionsRef.current = {};
      writePersisted({});
      setVersion((n) => n + 1);
      return;
    }
    if (!(toolName in decisionsRef.current)) { return; }
    const next = { ...decisionsRef.current };
    delete next[toolName];
    decisionsRef.current = next;
    writePersisted(next);
    setVersion((n) => n + 1);
  }, []);

  return { remember, recall, forget };
}

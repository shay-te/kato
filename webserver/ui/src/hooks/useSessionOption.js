import { useCallback, useEffect, useRef, useState } from 'react';
import { subscribeCatalogRefresh } from './useCatalogRefresh.js';
import { loadCatalog } from '../stores/catalogStore.js';

// Per-session option selector state, shared by the model picker and
// the effort picker in SessionDetail. Both pickers follow the exact
// same shape:
//
//   - fetch the option LIST once (loadedRef-guarded so it never
//     re-fetches per task switch — the catalogue is global),
//   - fetch the CURRENT value whenever the bound task changes,
//     resetting to '' when no task is bound,
//   - on change, optimistically set local state then POST the choice.
//
// The two differ only in the API functions and the result keys
// (``models`` vs ``levels`` for the list, ``model`` vs ``effort`` for
// the current value), so those are passed in as config.
//
// ``defaultKey`` (optional) names a top-level field on the option-list
// response that carries the concrete default the backend falls back to
// when the task has no explicit override (e.g. ``/api/effort-levels``
// returns ``{levels, default}``). It's surfaced so the picker can SHOW
// that concrete value instead of an ambiguous "Auto" — the effort
// equivalent of the model picker's per-option ``default: true`` flag.
//
// ``catalogKey`` (optional, default ``optionsKey``) names WHICH catalogue: the
// model list differs per agent (the Codex tab offered Claude's models), so the
// composer passes ``models:<agent>``. A new key loads that catalogue (cached
// per key) and the current value again — the pick is kept per agent too.
//
// Returns ``[options, selected, onChange, defaultValue]``.
export function useSessionOption(taskId, {
  fetchOptions,
  optionsKey,
  catalogKey = optionsKey,
  fetchCurrent,
  currentKey,
  setCurrent,
  defaultKey,
}) {
  const [options, setOptions] = useState([]);
  const [selected, setSelected] = useState('');
  const [defaultValue, setDefaultValue] = useState('');
  const loadedKeyRef = useRef(null);

  // Fetch + apply the option catalogue. ``force`` re-discovers server-side
  // (bypassing the version-label cache) — used by the on-demand refresh so a
  // just-upgraded CLI's labels show without a reload.
  const loadOptions = useCallback((force) => {
    // Cached at module level (keyed by ``optionsKey``) so a task switch — which
    // remounts this hook and resets ``loadedRef`` — reads the catalogue from
    // cache instead of re-hitting /models + /effort-levels every time. ``force``
    // (the header Refresh) bypasses + replaces the cache.
    loadCatalog(catalogKey, fetchOptions, force).then((result) => {
      if (result && Array.isArray(result[optionsKey])) {
        setOptions(result[optionsKey]);
      }
      if (defaultKey && result && result[defaultKey] != null) {
        setDefaultValue(String(result[defaultKey]));
      }
    }).catch(() => {});
  }, [fetchOptions, optionsKey, catalogKey, defaultKey]);

  // Fetch each catalogue once (per key). Guarded so task switches don't
  // re-hit the endpoint — a catalogue is the same for every task.
  useEffect(() => {
    if (loadedKeyRef.current === catalogKey) { return; }
    loadedKeyRef.current = catalogKey;
    loadOptions(false);
  }, [loadOptions, catalogKey]);

  // Re-fetch (forced) when a global catalogue refresh is broadcast — e.g. the
  // header Refresh after a CLI upgrade — so stale labels (the model version)
  // update live, no restart.
  useEffect(() => subscribeCatalogRefresh(() => loadOptions(true)), [loadOptions]);

  // Load the current value for the bound task; reset to '' with no task.
  useEffect(() => {
    if (!taskId) { setSelected(''); return; }
    fetchCurrent(taskId).then((result) => {
      setSelected((result && result[currentKey]) || '');
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId, catalogKey]);

  // Optimistically reflect the choice, then persist it.
  const onChange = useCallback(async (value) => {
    setSelected(value);
    await setCurrent(taskId, value);
  }, [taskId, setCurrent]);

  return [options, selected, onChange, defaultValue];
}

import { useCallback, useEffect, useState } from 'react';
import {
  toolDecisionsStore,
  TOOL_DECISIONS_STORAGE_KEY,
  readPersisted,
  writePersisted,
} from '../stores/toolDecisionsStore.js';

// React adapter over the shared ``toolDecisionsStore`` (the actual
// source of truth + persistence lives there now, so the permission
// prompt and the settings panel can never disagree). This hook only
// adds: a re-render on store change, cross-tab ``storage`` sync, and a
// stable callback surface.

// Re-exported for the existing persistence unit tests, which verify the
// localStorage layer independent of React. Underscore = test surface.
export const _readPersistedForTest = readPersisted;
export const _writePersistedForTest = writePersisted;

export function useToolMemory() {
  // ``version`` bumps on every store change so consumers re-render. It
  // is also a dep of ``recall``/``entries`` so their identity changes on
  // mutation — that's what lets App's attention memo (keyed on
  // ``toolMemory.recall``) recompute when a decision flips.
  const [version, setVersion] = useState(0);

  useEffect(
    () => toolDecisionsStore.subscribe(() => setVersion((n) => n + 1)),
    [],
  );

  // Cross-tab: another browser tab persisting a decision should affect
  // this tab too — otherwise the operator clicks "remember" once and the
  // OTHER open tab still shows the prompt.
  useEffect(() => {
    function onStorage(event) {
      if (event.key !== TOOL_DECISIONS_STORAGE_KEY) { return; }
      toolDecisionsStore.syncFromStorage();
    }
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  // ``command`` (optional) scopes a decision to an exact command for
  // command-keyed tools (Bash); omit it for tool-level decisions.
  const remember = useCallback(
    (toolName, allow, command = '') => toolDecisionsStore.remember(toolName, allow, command),
    [],
  );
  const recall = useCallback(
    (toolName, command = '') => toolDecisionsStore.recall(toolName, command),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [version],
  );
  const forget = useCallback(
    (toolName, command = '') => toolDecisionsStore.forget(toolName, command),
    [],
  );
  const entries = useCallback(
    () => toolDecisionsStore.entries(),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [version],
  );

  return { remember, recall, forget, entries };
}

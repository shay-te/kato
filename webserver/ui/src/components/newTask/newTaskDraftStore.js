// The "New task" draft, kept in this browser so a reload — or a closed
// laptop — never loses a half-written task. One draft at a time: the tab in
// the centre strip IS this draft (``open``), and creating the task or
// discarding it empties it again.
import { useSyncExternalStore } from 'react';
import { createPreferenceStore } from '../../utils/createPreferenceStore.js';
import { EMPTY_DRAFT, coerceDraft } from './newTaskHelpers.js';

const _store = createPreferenceStore({
  key: 'kato.newTaskDraft.v1',
  defaults: EMPTY_DRAFT,
  coerce: (parsed) => coerceDraft(parsed),
});

export function readNewTaskDraft() {
  return _store.read();
}

export function updateNewTaskDraft(patch) {
  return _store.write({ ..._store.read(), ...patch });
}

export function openNewTaskDraft() {
  return updateNewTaskDraft({ open: true });
}

// Close the tab and forget what was written.
export function discardNewTaskDraft() {
  return _store.write(EMPTY_DRAFT);
}

export function useNewTaskDraft() {
  return useSyncExternalStore(_store.subscribe, readNewTaskDraft, readNewTaskDraft);
}

// Test-only: the cache and listeners are module-level.
export function _resetNewTaskDraft() {
  _store.reset();
}

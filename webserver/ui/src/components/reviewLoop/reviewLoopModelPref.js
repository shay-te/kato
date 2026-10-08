// Which model the review loop's reviews run on — the operator's pick, remembered.
//
// Chosen in the review loop view beside the round limit, before Start / Run
// again, and kept for every later loop on any task. '' means "the reviewer's
// default": the picker shows that model by name (never "Default"), and Start
// sends no model, so the server runs exactly the one shown. Picking the
// default entry stores '' too, so a later change to kato's own model setting
// is followed rather than frozen here.
import { useSyncExternalStore } from 'react';
import { createPreferenceStore } from '../../utils/createPreferenceStore.js';

const _store = createPreferenceStore({
  key: 'kato.reviewLoopModel.v1',
  defaults: { model: '' },
  coerce: (parsed) => ({ model: typeof parsed.model === 'string' ? parsed.model.trim() : '' }),
});

export function readReviewLoopModel() {
  return _store.read().model;
}

export function writeReviewLoopModel(model) {
  return _store.write({ model }).model;
}

export function useReviewLoopModel() {
  return useSyncExternalStore(_store.subscribe, readReviewLoopModel, readReviewLoopModel);
}

// Test-only: the cache and listeners are module-level.
export function _resetReviewLoopModel() {
  _store.reset();
}

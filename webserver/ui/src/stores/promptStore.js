// Operator overrides for the predefined chat prompts. The DEFAULT text
// ships as a .md file (predefined_prompts/); this store lets the operator
// edit it from Settings → Prompts and persists the override to
// localStorage. The code-review button resolves the EFFECTIVE text
// (override if set, else default) in the browser at click time, so an
// override applies immediately with no backend round-trip.
//
// Shared pub/sub (same shape as toolDecisionsStore) so the Settings panel
// and the button always read one source.

import { readStorageString, writeStorageItem } from '../utils/storage.js';
import { parseJsonOr } from '../utils/json.js';
import { createPubSub } from './pubsub.js';
import { PREDEFINED_PROMPTS } from '../predefined_prompts/index.js';

export const PROMPT_OVERRIDES_STORAGE_KEY = 'kato.promptOverrides.v1';

// The prompts the operator may override, in display order. ``id`` is the
// stable key (also the PREDEFINED_PROMPTS key); ``default`` is the shipped
// .md text. Add a row here + a .md in predefined_prompts/ to expose more.
export const EDITABLE_PROMPTS = Object.freeze([
  {
    id: 'codeReview',
    label: 'Code review',
    description: 'Sent by the "Code review" toolbar button (the diff icon).',
    default: PREDEFINED_PROMPTS.codeReview,
  },
]);

function _defaultFor(id) {
  const meta = EDITABLE_PROMPTS.find((p) => p.id === id);
  return meta ? meta.default : '';
}

function readOverrides() {
  const parsed = parseJsonOr(
    readStorageString(PROMPT_OVERRIDES_STORAGE_KEY, null), null,
  );
  if (!parsed || typeof parsed !== 'object') { return {}; }
  return parsed;
}

function writeOverrides(overrides) {
  writeStorageItem(PROMPT_OVERRIDES_STORAGE_KEY, JSON.stringify(overrides), undefined);
}

let _overrides = readOverrides();
const _pubsub = createPubSub(() => _overrides);

function _commit(next) {
  _overrides = next;
  writeOverrides(next);
  _pubsub.emit();
}

export const promptStore = {
  subscribe: _pubsub.subscribe,

  // Effective text for ``id``: a non-blank override, else the shipped default.
  get(id) {
    const override = String(_overrides[id] || '').trim();
    return override || _defaultFor(id);
  },

  // The raw saved override ('' when none) — what the editor textarea shows.
  override(id) {
    return String(_overrides[id] || '');
  },

  isCustom(id) {
    return !!String(_overrides[id] || '').trim();
  },

  // Save an override. A blank/whitespace value resets to the default
  // (so "clear the box + save" reverts cleanly).
  setOverride(id, text) {
    if (!id) { return; }
    const value = String(text || '');
    if (!value.trim()) { this.reset(id); return; }
    if (_overrides[id] === value) { return; }
    _commit({ ..._overrides, [id]: value });
  },

  reset(id) {
    if (!id || !(id in _overrides)) { return; }
    const next = { ..._overrides };
    delete next[id];
    _commit(next);
  },

  // Cross-tab sync.
  syncFromStorage() {
    _overrides = readOverrides();
    _pubsub.emit();
  },
};

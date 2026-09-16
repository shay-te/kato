// The operator's fast prompts — one-click prompts on the session toolbar.
//
// Code review ships with kato (its text is predefined_prompts/code_review.md).
// The operator can change any prompt's name, icon and text, and add prompts of
// their own. Everything is saved in this browser (localStorage) and read at
// click time, so an edit applies to the very next click with no backend
// round-trip.
//
// Shared pub/sub (same shape as toolDecisionsStore) so the Settings panel and
// the toolbar always read one source.

import { useSyncExternalStore } from 'react';
import { readStorageString, writeStorageItem } from '../utils/storage.js';
import { parseJsonOr } from '../utils/json.js';
import { createPubSub } from './pubsub.js';
import { PREDEFINED_PROMPTS } from '../predefined_prompts/index.js';

export const FAST_PROMPTS_STORAGE_KEY = 'kato.fastPrompts.v1';
// Before prompts could be added, only Code review's TEXT could be changed, and
// it was saved here. Read when nothing is saved under the new key, so an
// upgrade keeps the operator's review prompt.
export const LEGACY_PROMPT_OVERRIDES_STORAGE_KEY = 'kato.promptOverrides.v1';

// The icons a prompt may use: the line icons that read as an action on a
// toolbar (no chevrons, spinners or aliases).
export const FAST_PROMPT_ICONS = Object.freeze([
  'diff', 'code', 'search', 'eye', 'check', 'check-double', 'edit', 'comment',
  'send', 'file', 'warning', 'bell', 'pin', 'history', 'refresh', 'merge',
  'pull-request', 'commit', 'gear', 'crosshair',
]);

const NEW_PROMPT_ICON = 'send';

// The prompts kato ships. The operator's changes are saved against ``id``, so
// "Reset to default" brings these values back.
export const BUILTIN_PROMPTS = Object.freeze([
  Object.freeze({
    id: 'codeReview',
    label: 'Code review',
    icon: 'diff',
    text: PREDEFINED_PROMPTS.codeReview,
  }),
]);

function isPlainObject(value) {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

// ``{label, icon, text}`` a prompt may be saved with, or null when the name or
// the text is blank — a toolbar button that sends nothing is not a prompt.
function validFields(fields, fallbackIcon) {
  const label = String(fields?.label ?? '').trim();
  const text = String(fields?.text ?? '');
  if (!label || !text.trim()) { return null; }
  const icon = FAST_PROMPT_ICONS.includes(fields?.icon) ? fields.icon : fallbackIcon;
  return { label, icon, text };
}

function readState() {
  const saved = parseJsonOr(readStorageString(FAST_PROMPTS_STORAGE_KEY, null), null);
  if (isPlainObject(saved)) {
    return {
      builtins: isPlainObject(saved.builtins) ? saved.builtins : {},
      custom: (Array.isArray(saved.custom) ? saved.custom : [])
        .map((prompt) => {
          const fields = validFields(prompt, NEW_PROMPT_ICON);
          return fields && typeof prompt.id === 'string' && prompt.id
            ? { id: prompt.id, ...fields }
            : null;
        })
        .filter(Boolean),
    };
  }
  const legacy = parseJsonOr(
    readStorageString(LEGACY_PROMPT_OVERRIDES_STORAGE_KEY, null), null,
  );
  const builtins = {};
  for (const { id } of BUILTIN_PROMPTS) {
    if (isPlainObject(legacy) && String(legacy[id] || '').trim()) {
      builtins[id] = { text: String(legacy[id]) };
    }
  }
  return { builtins, custom: [] };
}

function buildList(state) {
  const builtins = BUILTIN_PROMPTS.map((base) => {
    const saved = state.builtins[base.id] || {};
    const label = String(saved.label || '').trim() || base.label;
    const icon = FAST_PROMPT_ICONS.includes(saved.icon) ? saved.icon : base.icon;
    const text = String(saved.text || '').trim() ? String(saved.text) : base.text;
    return Object.freeze({
      id: base.id,
      label,
      icon,
      text,
      builtin: true,
      changed: label !== base.label || icon !== base.icon || text !== base.text,
    });
  });
  const custom = state.custom.map((prompt) => Object.freeze({
    ...prompt, builtin: false, changed: false,
  }));
  return Object.freeze([...builtins, ...custom]);
}

let _state = readState();
let _list = buildList(_state);
const _pubsub = createPubSub(() => _state);

function _commit(next) {
  if (JSON.stringify(next) === JSON.stringify(_state)) { return; }
  _state = next;
  _list = buildList(next);
  writeStorageItem(FAST_PROMPTS_STORAGE_KEY, JSON.stringify(next), undefined);
  _pubsub.emit();
}

function _newId() {
  return `custom-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

export const promptStore = {
  subscribe: _pubsub.subscribe,

  // Every prompt in toolbar order — the shipped ones first, then the
  // operator's in the order they were added. Each is
  // ``{id, label, icon, text, builtin, changed}``; the array is stable between
  // changes, so it can be a ``useSyncExternalStore`` snapshot.
  list() {
    return _list;
  },

  // The text a prompt sends ('' for an unknown id).
  get(id) {
    const prompt = _list.find((entry) => entry.id === id);
    return prompt ? prompt.text : '';
  },

  // Save a prompt's name, icon and text. A shipped prompt stores only what
  // differs from its default, so saving the default back is a reset. Returns
  // false when nothing was saved (unknown id, blank name or text).
  save(id, fields) {
    const base = BUILTIN_PROMPTS.find((prompt) => prompt.id === id);
    if (base) {
      const valid = validFields(fields, base.icon);
      if (!valid) { return false; }
      const changes = {};
      for (const key of ['label', 'icon', 'text']) {
        if (valid[key] !== base[key]) { changes[key] = valid[key]; }
      }
      const builtins = { ..._state.builtins };
      if (Object.keys(changes).length > 0) { builtins[id] = changes; } else { delete builtins[id]; }
      _commit({ ..._state, builtins });
      return true;
    }
    const current = _state.custom.find((prompt) => prompt.id === id);
    const valid = current && validFields(fields, current.icon);
    if (!valid) { return false; }
    _commit({
      ..._state,
      custom: _state.custom.map((prompt) => (prompt.id === id ? { id, ...valid } : prompt)),
    });
    return true;
  },

  // Change ONLY a prompt's icon, and save it there and then.
  //
  // Picking an icon is a single click with a visible result, so making it wait
  // behind Save read as a picker that does not work ("still i can't change the
  // prompt icon"). The name and the text stay save-on-submit: those are edits
  // in progress, and a half-typed name must not reach the toolbar.
  setIcon(id, icon) {
    if (!FAST_PROMPT_ICONS.includes(icon)) { return false; }
    const prompt = _list.find((entry) => entry.id === id);
    if (!prompt) { return false; }
    return this.save(id, { label: prompt.label, icon, text: prompt.text });
  },

  // Add an operator prompt; its new id, or '' when the name or text is blank.
  add(fields) {
    const valid = validFields(fields, NEW_PROMPT_ICON);
    if (!valid) { return ''; }
    const id = _newId();
    _commit({ ..._state, custom: [..._state.custom, { id, ...valid }] });
    return id;
  },

  // Delete an operator prompt. The shipped ones cannot be deleted, only reset.
  remove(id) {
    if (!_state.custom.some((prompt) => prompt.id === id)) { return false; }
    _commit({ ..._state, custom: _state.custom.filter((prompt) => prompt.id !== id) });
    return true;
  },

  // Put a shipped prompt back to its default name, icon and text.
  reset(id) {
    if (!(id in _state.builtins)) { return; }
    const builtins = { ..._state.builtins };
    delete builtins[id];
    _commit({ ..._state, builtins });
  },
};

// The prompt list, re-rendering on every change.
export function useFastPrompts() {
  return useSyncExternalStore(promptStore.subscribe, promptStore.list, promptStore.list);
}

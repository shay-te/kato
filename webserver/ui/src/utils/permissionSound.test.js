// Tests for the approval-sound preferences + gating. The Web Audio synth
// itself is stubbed (no real AudioContext in node:test); we assert WHEN a
// chime is requested, honouring enabled / focus-mode / dedupe.

import assert from 'node:assert/strict';
import test, { beforeEach, afterEach } from 'node:test';

import {
  readPermissionSoundPrefs,
  writePermissionSoundPrefs,
  maybePlayPermissionChime,
  _resetPermissionSoundPrefs,
} from './permissionSound.js';

// --- minimal browser shims -------------------------------------------------

let _store = {};
let _plays = 0;

function installShims({ focused = true, visible = true } = {}) {
  globalThis.localStorage = {
    getItem: (k) => (k in _store ? _store[k] : null),
    setItem: (k, v) => { _store[k] = String(v); },
    removeItem: (k) => { delete _store[k]; },
  };
  globalThis.document = {
    visibilityState: visible ? 'visible' : 'hidden',
    hasFocus: () => focused,
  };
  // Count oscillator creations as "a chime played".
  const osc = () => ({
    type: '', frequency: { value: 0 }, connect: () => osc2(),
    start: () => {}, stop: () => {},
  });
  const osc2 = () => ({ connect: () => ({}) });
  globalThis.window = {
    AudioContext: class {
      constructor() { this.state = 'running'; this.currentTime = 0; this.destination = {}; }
      createOscillator() { _plays += 1; return osc(); }
      createGain() {
        return {
          gain: {
            setValueAtTime: () => {},
            exponentialRampToValueAtTime: () => {},
          },
          connect: () => ({ connect: () => ({}) }),
        };
      }
      resume() { return Promise.resolve(); }
    },
  };
}

beforeEach(() => {
  _store = {};
  _plays = 0;
  // Clear the module-level pref cache and the chime dedupe map. Every case
  // used to have to write a known value first, because there was no way to
  // reset — a case that only READ inherited the previous case's prefs.
  _resetPermissionSoundPrefs();
});

afterEach(() => {
  delete globalThis.localStorage;
  delete globalThis.document;
  delete globalThis.window;
});


test('defaults: enabled + only-when-unfocused', () => {
  installShims();
  const prefs = writePermissionSoundPrefs({ enabled: true, onlyWhenUnfocused: true });
  assert.equal(prefs.enabled, true);
  assert.equal(prefs.onlyWhenUnfocused, true);
  assert.deepEqual(readPermissionSoundPrefs(), prefs);
});

test('disabled → no chime, even when unfocused', () => {
  installShims({ focused: false });
  writePermissionSoundPrefs({ enabled: false, onlyWhenUnfocused: false });
  maybePlayPermissionChime('a');
  assert.equal(_plays, 0);
});

test('only-when-unfocused: silent while focused', () => {
  installShims({ focused: true, visible: true });
  writePermissionSoundPrefs({ enabled: true, onlyWhenUnfocused: true });
  maybePlayPermissionChime('req-1');
  assert.equal(_plays, 0);
});

test('only-when-unfocused: chimes when the window is blurred', () => {
  installShims({ focused: false, visible: true });
  writePermissionSoundPrefs({ enabled: true, onlyWhenUnfocused: true });
  maybePlayPermissionChime('req-2');
  assert.ok(_plays > 0);
});

test('only-when-unfocused: chimes when the tab is hidden', () => {
  installShims({ focused: true, visible: false });
  writePermissionSoundPrefs({ enabled: true, onlyWhenUnfocused: true });
  maybePlayPermissionChime('req-3');
  assert.ok(_plays > 0);
});

test('always mode: chimes even while focused', () => {
  installShims({ focused: true, visible: true });
  writePermissionSoundPrefs({ enabled: true, onlyWhenUnfocused: false });
  maybePlayPermissionChime('req-4');
  assert.ok(_plays > 0);
});

test('dedupe: the same request id within the window chimes once', () => {
  installShims({ focused: true });
  writePermissionSoundPrefs({ enabled: true, onlyWhenUnfocused: false });
  maybePlayPermissionChime('dupe');
  const after1 = _plays;
  assert.ok(after1 > 0);
  maybePlayPermissionChime('dupe'); // SSE + status feed race → still one
  assert.equal(_plays, after1);
});

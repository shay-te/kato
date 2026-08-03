// A light, friendly chime when a permission ask needs the operator's
// attention — plus the operator's preferences for it.
//
// Two knobs, both operator-controlled from Settings:
//   * ``enabled``            — play a sound at all.
//   * ``onlyWhenUnfocused``  — play ONLY when the kato tab/window isn't
//                              focused (so it nudges you when you've looked
//                              away) vs. always (even while you're watching).
//
// The sound is synthesised with the Web Audio API — no asset files, because
// the app's CSP blocks external media and bundling audio is overkill for a
// two-note chime.

import { createPreferenceStore } from './createPreferenceStore.js';

const STORAGE_KEY = 'kato.permissionSound.v1';
const DEFAULTS = { enabled: true, onlyWhenUnfocused: true };
// Collapse the SSE + status-feed double-emit of the same ask (and reconnect
// backlog bursts) into one chime.
const DEDUPE_WINDOW_MS = 2000;

const _store = createPreferenceStore({
  key: STORAGE_KEY,
  defaults: DEFAULTS,
  coerce: (parsed, defaults) => ({
    enabled: parsed.enabled === undefined ? defaults.enabled : !!parsed.enabled,
    onlyWhenUnfocused: parsed.onlyWhenUnfocused === undefined
      ? defaults.onlyWhenUnfocused : !!parsed.onlyWhenUnfocused,
  }),
});

const _recent = new Map(); // key -> last-played epoch ms

export function readPermissionSoundPrefs() {
  return _store.read();
}

export function writePermissionSoundPrefs(next) {
  return _store.write(next);
}

export function subscribePermissionSoundPrefs(fn) {
  return _store.subscribe(fn);
}

// Test-only: the cache + listeners are module-level, so tests must reset
// between cases for isolation. This module previously had NO reset while its
// twin did — a module-level cache with no way to clear it leaks between test
// cases and shows up as an unrelated test going red later.
export function _resetPermissionSoundPrefs() {
  _store.reset();
  _recent.clear();
}

// True when the kato tab/window is the operator's focus. Hidden tab or a
// blurred window both count as "not looking".
function _windowFocused() {
  if (typeof document === 'undefined') { return true; }
  if (document.visibilityState === 'hidden') { return false; }
  if (typeof document.hasFocus === 'function') { return document.hasFocus(); }
  return true;
}

let _audioCtx = null;
function _context() {
  if (_audioCtx) { return _audioCtx; }
  if (typeof window === 'undefined') { return null; }
  const AudioCtor = window.AudioContext || window.webkitAudioContext;
  if (!AudioCtor) { return null; }
  try { _audioCtx = new AudioCtor(); } catch (_) { return null; }
  return _audioCtx;
}

// A soft rising two-note chime ("ti-doo"). Gentle attack/decay so it reads
// as a friendly ding, not a harsh beep.
export function playPermissionChime() {
  const ctx = _context();
  if (!ctx) { return; }
  // Autoplay policy suspends the context until a user gesture; resuming is a
  // no-op once the operator has clicked anywhere in the app (they always
  // have, by the time a permission ask appears).
  if (ctx.state === 'suspended' && typeof ctx.resume === 'function') {
    ctx.resume().catch(() => {});
  }
  const start = ctx.currentTime;
  const notes = [{ freq: 660, at: 0 }, { freq: 880, at: 0.12 }];
  for (const { freq, at } of notes) {
    try {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.value = freq;
      const t0 = start + at;
      gain.gain.setValueAtTime(0.0001, t0);
      gain.gain.exponentialRampToValueAtTime(0.14, t0 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.35);
      osc.connect(gain).connect(ctx.destination);
      osc.start(t0);
      osc.stop(t0 + 0.4);
    } catch (_) { /* one note failing shouldn't throw into the caller */ }
  }
}

function _recentlyPlayed(key, now) {
  // Opportunistic cleanup so the map can't grow unbounded.
  for (const [k, ts] of _recent) {
    if (now - ts > DEDUPE_WINDOW_MS) { _recent.delete(k); }
  }
  if (!key) { return false; }
  const last = _recent.get(key);
  return last !== undefined && now - last < DEDUPE_WINDOW_MS;
}

// Play the chime for a fresh permission ask, honouring the operator's prefs.
// ``key`` (a request id, or ``taskId:tool``) dedupes the same ask arriving on
// two channels within a short window. Never throws.
export function maybePlayPermissionChime(key = '') {
  try {
    const prefs = readPermissionSoundPrefs();
    if (!prefs.enabled) { return; }
    if (prefs.onlyWhenUnfocused && _windowFocused()) { return; }
    const now = Date.now();
    if (_recentlyPlayed(key, now)) { return; }
    if (key) { _recent.set(key, now); }
    playPermissionChime();
  } catch (_) { /* a sound must never break the notification path */ }
}

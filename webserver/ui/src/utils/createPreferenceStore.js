// Factory for a client-side operator preference: a small JSON record in
// localStorage, cached in the module, with subscribers notified on write.
//
// Two modules had grown this same 40-line mechanism independently
// (``permissionSound``, ``composerSteerPref``) and they were not identical:
// one exported a ``_reset`` for test isolation and one did not. Without it a
// module-level cache leaks between test cases, so a test that never writes
// silently reads whatever the previous test left behind — the failure shows
// up as an unrelated test going red later, in a different file.
//
// What a caller still owns is the part that genuinely differs: the storage
// key, the defaults, and how a parsed value is coerced. Everything below —
// caching, the parse/try/catch, subscriber fan-out, subscriber isolation,
// reset — is the same for every preference and lives here once.
//
// Persistence is best-effort by design: a failed write (private mode, quota)
// keeps the in-memory value and degrades to "the next reload shows the
// default", never a crash.

import { readStorageString, writeStorageItem } from './storage.js';

// ``key``      — localStorage key, e.g. 'kato.permissionSound.v1'
// ``defaults`` — the record returned when nothing is stored or parsing fails
// ``coerce``   — (parsedRecord, defaults) => record. Called with ``{}`` when
//                storage is empty, so it must be total. Owns per-field
//                defaulting and type coercion.
//
// The stored shape is always a RECORD, never a bare scalar, and ``coerce``
// must return one. That is not stylistic: ``JSON.parse('false')`` yields a
// non-object, which a "restore the defaults" guard cannot distinguish from
// corrupt storage — so a preference persisted as a bare ``false`` reads back
// as its default forever, and only on the value the operator explicitly
// chose. A caller exposing a single flag projects the field at its own
// boundary (see ``composerSteerPref``).
//
// Returns ``{ read, write, subscribe, reset }``.
export function createPreferenceStore({ key, defaults, coerce }) {
  let cache = null;
  const listeners = new Set();

  function read() {
    if (cache !== null) { return cache; }
    let parsed = {};
    try {
      const raw = readStorageString(key, '');
      parsed = raw ? JSON.parse(raw) : {};
      if (!parsed || typeof parsed !== 'object') { parsed = {}; }
    } catch (_err) {
      // Corrupt / non-JSON value — fall back to the defaults rather than
      // letting a bad byte in storage break the app on every read.
      parsed = {};
    }
    cache = coerce(parsed, defaults);
    return cache;
  }

  function write(next) {
    cache = coerce(next === null || next === undefined ? {} : next, defaults);
    writeStorageItem(key, JSON.stringify(cache));
    for (const fn of listeners) {
      // Isolate a throwing subscriber: one bad listener must not stop the
      // others from seeing the change, nor surface as a failed write.
      try { fn(cache); } catch (_err) { /* isolate */ }
    }
    return cache;
  }

  function subscribe(fn) {
    listeners.add(fn);
    return () => { listeners.delete(fn); };
  }

  // Test-only. The cache and listeners are module-level, so tests MUST reset
  // between cases or state leaks across them.
  function reset() {
    cache = null;
    listeners.clear();
  }

  return { read, write, subscribe, reset };
}

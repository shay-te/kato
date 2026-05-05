// Persistence guarantee for the tool-permission memory hook. The
// operator pain that drove this is real: re-prompting for git after
// every kato restart. So the cardinal property — "remember(toolName,
// true) survives a fresh hook mount" — has its own test.
//
// Light browser-API stubs: ``window.localStorage`` and the ``storage``
// event listener. We don't pull in a full DOM (jsdom would slow the
// suite); we just give the hook the surface area it actually touches.

import { describe, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';

class FakeLocalStorage {
  constructor() { this._data = new Map(); }
  getItem(key) {
    return this._data.has(key) ? this._data.get(key) : null;
  }
  setItem(key, value) { this._data.set(key, String(value)); }
  removeItem(key) { this._data.delete(key); }
  clear() { this._data.clear(); }
}

function installFakeWindow() {
  global.window = {
    localStorage: new FakeLocalStorage(),
    addEventListener: () => {},
    removeEventListener: () => {},
  };
}

function uninstallFakeWindow() {
  delete global.window;
}

describe('useToolMemory localStorage persistence', () => {
  beforeEach(() => {
    installFakeWindow();
  });

  test('remember(allow) writes a serializable record to localStorage', async () => {
    const mod = await freshImport();
    // Mimic what the React hook does: read existing, merge, write.
    const initial = mod._readPersistedForTest();
    assert.deepEqual(initial, {});
    mod._writePersistedForTest({ ...initial, Bash: 'allow' });
    const persisted = mod._readPersistedForTest();
    assert.deepEqual(persisted, { Bash: 'allow' });
    // Verify it's stored as JSON under the canonical key — that's
    // what makes survives-restart durable. Anything else (sessionStorage,
    // in-memory only) loses across a kato restart.
    const raw = window.localStorage.getItem('kato.toolDecisions.v1');
    assert.equal(typeof raw, 'string');
    assert.deepEqual(JSON.parse(raw), { Bash: 'allow' });
    uninstallFakeWindow();
  });

  test('readPersisted returns the stored shape on a fresh mount (the restart case)', async () => {
    const mod = await freshImport();
    // Pre-seed localStorage as if a previous kato run wrote here.
    window.localStorage.setItem(
      'kato.toolDecisions.v1',
      JSON.stringify({ Bash: 'allow', Edit: 'deny' }),
    );
    const persisted = mod._readPersistedForTest();
    assert.deepEqual(persisted, { Bash: 'allow', Edit: 'deny' });
    uninstallFakeWindow();
  });

  test('readPersisted tolerates missing / non-JSON / non-object values', async () => {
    const mod = await freshImport();
    // Missing key.
    assert.deepEqual(mod._readPersistedForTest(), {});
    // Non-JSON garbage.
    window.localStorage.setItem('kato.toolDecisions.v1', 'not-json');
    assert.deepEqual(mod._readPersistedForTest(), {});
    // JSON but not an object (e.g. an array somebody put there).
    window.localStorage.setItem('kato.toolDecisions.v1', '[1,2,3]');
    // Arrays ARE objects in JS, but we accept them — at worst recall
    // returns null for any tool name that isn't a numeric index.
    // What we care about is "doesn't throw", which it doesn't.
    const result = mod._readPersistedForTest();
    assert.equal(typeof result, 'object');
    uninstallFakeWindow();
  });

  test('writePersisted is a no-op when window.localStorage is unavailable', async () => {
    const mod = await freshImport();
    delete window.localStorage;
    // Must not throw — private-mode browsers, SSR contexts, etc.
    mod._writePersistedForTest({ Bash: 'allow' });
    uninstallFakeWindow();
  });
});

// Re-import the module fresh so the in-memory module state doesn't
// bleed across tests. Node's ESM cache makes this awkward — we use a
// query-string suffix to force a new copy.
async function freshImport() {
  const url = `./useToolMemory.js?t=${Date.now()}-${Math.random()}`;
  return await import(url);
}

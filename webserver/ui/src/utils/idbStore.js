// Best-effort IndexedDB key-value store.
//
// Used to persist the composer's IMAGE attachments and any queued-message
// payloads that carry images. Those are base64 data that blows localStorage's
// ~5 MB quota, but fits comfortably in IndexedDB. Unlike the in-memory
// module-level caches (which only survive tab switches within a page load),
// IndexedDB survives a full page reload too.
//
// Every operation is BEST-EFFORT: if IndexedDB is unavailable (private
// browsing, disabled, a non-browser/node test environment) or any request
// fails, the promise resolves to ``undefined`` and the caller carries on with
// no persistence rather than throwing. So the composer always works — it just
// loses cross-reload image persistence when storage is unavailable.

const DB_NAME = 'kato-composer';
const DB_VERSION = 1;
const STORE = 'kv';

let _dbPromise = null;

function _openDb() {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      reject(new Error('indexedDB unavailable'));
      return;
    }
    let req;
    try {
      req = indexedDB.open(DB_NAME, DB_VERSION);
    } catch (err) {
      reject(err);
      return;
    }
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE);
      }
    };
    req.onsuccess = () => {
      const db = req.result;
      // If another tab opens at a higher version, step aside so it isn't
      // blocked; and if THIS connection is ever closed (version change,
      // browser idle-eviction), drop the cached promise so the next op
      // re-opens instead of silently no-op'ing every write for the rest of
      // the page session against a dead handle.
      db.onversionchange = () => { db.close(); _dbPromise = null; };
      db.onclose = () => { _dbPromise = null; };
      resolve(db);
    };
    req.onerror = () => reject(req.error || new Error('indexedDB open failed'));
  });
}

function _db() {
  if (!_dbPromise) {
    _dbPromise = _openDb().catch((err) => {
      // Reset so a later call can retry (e.g. the user re-enables storage).
      _dbPromise = null;
      throw err;
    });
  }
  return _dbPromise;
}

function _run(mode, run) {
  return _db()
    .then((db) => new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, mode);
      const store = tx.objectStore(STORE);
      let result;
      const req = run(store);
      if (req) {
        req.onsuccess = () => { result = req.result; };
      }
      tx.oncomplete = () => resolve(result);
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    }))
    .catch(() => undefined); // best-effort: swallow and no-op
}

export function idbGet(key) {
  if (!key) { return Promise.resolve(undefined); }
  return _run('readonly', (store) => store.get(key));
}

export function idbSet(key, value) {
  if (!key) { return Promise.resolve(undefined); }
  return _run('readwrite', (store) => store.put(value, key));
}

export function idbDelete(key) {
  if (!key) { return Promise.resolve(undefined); }
  return _run('readwrite', (store) => store.delete(key));
}

// Test-only: drop the cached connection so a test can swap the global
// ``indexedDB`` (or simulate it being unavailable) between cases.
export function _resetIdbConnection() {
  _dbPromise = null;
}

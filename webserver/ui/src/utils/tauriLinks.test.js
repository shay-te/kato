// Tests for the desktop-shell external-link opener. No real DOM: we shim
// `window` (Tauri detection + location) and pass a fake `document` whose
// addEventListener captures the handler so we can drive synthetic clicks.

import assert from 'node:assert/strict';
import test, { beforeEach, afterEach } from 'node:test';

import {
  openExternalUrl,
  isExternalHttpLink,
  installTauriExternalLinks,
  _resetTauriExternalLinks,
} from './tauriLinks.js';

const origWindow = globalThis.window;

function setWindow(tauri) {
  globalThis.window = {
    __TAURI__: tauri || undefined,
    location: { href: 'http://127.0.0.1:5050/', host: '127.0.0.1:5050' },
  };
}

beforeEach(() => {
  _resetTauriExternalLinks();
  setWindow(undefined);
});

afterEach(() => {
  globalThis.window = origWindow;
});

test('isExternalHttpLink: external https is external', () => {
  setWindow(undefined);
  assert.equal(isExternalHttpLink('https://paddle.com/docs'), true);
});

test('isExternalHttpLink: same-host link is NOT external', () => {
  setWindow(undefined);
  assert.equal(isExternalHttpLink('http://127.0.0.1:5050/api/x'), false);
});

test('isExternalHttpLink: relative + non-http are not external', () => {
  setWindow(undefined);
  assert.equal(isExternalHttpLink('/tasks/UNA-1'), false);
  assert.equal(isExternalHttpLink('mailto:a@b.com'), false);
  assert.equal(isExternalHttpLink(''), false);
});

test('openExternalUrl: prefers opener.openUrl', () => {
  const calls = [];
  setWindow({ opener: { openUrl: (u) => calls.push(['opener', u]) } });
  assert.equal(openExternalUrl('https://x.com'), true);
  assert.deepEqual(calls, [['opener', 'https://x.com']]);
});

test('openExternalUrl: falls back to core.invoke opener command', () => {
  const calls = [];
  setWindow({ core: { invoke: (cmd, args) => calls.push([cmd, args]) } });
  assert.equal(openExternalUrl('https://x.com'), true);
  assert.deepEqual(calls, [['plugin:opener|open_url', { url: 'https://x.com' }]]);
});

test('openExternalUrl: falls back to shell.open', () => {
  const calls = [];
  setWindow({ shell: { open: (u) => calls.push(u) } });
  assert.equal(openExternalUrl('https://x.com'), true);
  assert.deepEqual(calls, ['https://x.com']);
});

test('openExternalUrl: no Tauri -> false', () => {
  setWindow(undefined);
  assert.equal(openExternalUrl('https://x.com'), false);
});

// --- install + click delegation ------------------------------------------

function fakeDoc() {
  let handler = null;
  return {
    addEventListener: (type, fn, capture) => {
      if (type === 'click') { handler = { fn, capture }; }
    },
    fire: (event) => handler && handler.fn(event),
    hasHandler: () => handler !== null,
  };
}

function clickEvent(href) {
  const anchor = href === null ? null : { getAttribute: () => href };
  let prevented = false;
  return {
    button: 0,
    defaultPrevented: false,
    target: { closest: (sel) => (sel === 'a[href]' ? anchor : null) },
    preventDefault: () => { prevented = true; },
    wasPrevented: () => prevented,
  };
}

test('install is a no-op outside Tauri (no handler bound)', () => {
  setWindow(undefined);
  const doc = fakeDoc();
  installTauriExternalLinks(doc);
  assert.equal(doc.hasHandler(), false);
});

test('inside Tauri: external link click is intercepted + opened', () => {
  const opened = [];
  setWindow({ opener: { openUrl: (u) => opened.push(u) } });
  const doc = fakeDoc();
  installTauriExternalLinks(doc);
  assert.equal(doc.hasHandler(), true);

  const ev = clickEvent('https://paddle.com/docs');
  doc.fire(ev);
  assert.deepEqual(opened, ['https://paddle.com/docs']);
  assert.equal(ev.wasPrevented(), true);
});

test('inside Tauri: internal + non-anchor clicks are left alone', () => {
  const opened = [];
  setWindow({ opener: { openUrl: (u) => opened.push(u) } });
  const doc = fakeDoc();
  installTauriExternalLinks(doc);

  const internal = clickEvent('http://127.0.0.1:5050/tasks/x');
  doc.fire(internal);
  const none = clickEvent(null);
  doc.fire(none);

  assert.deepEqual(opened, []);
  assert.equal(internal.wasPrevented(), false);
});

test('install is idempotent (guard prevents double-binding)', () => {
  setWindow({ opener: { openUrl: () => {} } });
  const doc = fakeDoc();
  let binds = 0;
  const countingDoc = {
    addEventListener: (t) => { if (t === 'click') binds += 1; },
  };
  installTauriExternalLinks(countingDoc);
  installTauriExternalLinks(countingDoc);
  assert.equal(binds, 1);
});

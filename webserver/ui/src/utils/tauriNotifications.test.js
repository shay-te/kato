// Tests for the native-notification bridge. In a browser every function is a
// no-op; inside the desktop shell they must reach the notification plugin
// using the exact command names + payload shape the Rust side expects
// (verified against tauri-plugin-notification 2.3.3).
import { describe, test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import {
  isTauriPermissionGranted,
  isTauriShell,
  requestTauriPermission,
  sendTauriNotification,
} from './tauriNotifications.js';

let calls;

function setTauri(api) {
  globalThis.window = { __TAURI__: api };
}

beforeEach(() => { calls = []; });
afterEach(() => { delete globalThis.window; });

function invokeApi(responses = {}) {
  return {
    core: {
      invoke: async (command, payload) => {
        calls.push({ command, payload });
        return responses[command];
      },
    },
  };
}

describe('outside the desktop shell', () => {
  test('every entry point is inert', async () => {
    globalThis.window = {};
    assert.equal(isTauriShell(), false);
    assert.equal(sendTauriNotification({ title: 'x' }), false);
    assert.equal(await isTauriPermissionGranted(), false);
    // Nothing to ask, so nothing is granted — never throws.
    assert.equal(await requestTauriPermission(), 'denied');
  });
});

describe('inside the desktop shell', () => {
  test('sends via the plugin command with the options payload', () => {
    setTauri(invokeApi());
    assert.equal(sendTauriNotification({ title: 'Approval', body: 'T1' }), true);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].command, 'plugin:notification|notify');
    assert.deepEqual(calls[0].payload, {
      options: { title: 'Approval', body: 'T1' },
    });
  });

  test('coerces a missing body/title rather than sending undefined', () => {
    setTauri(invokeApi());
    sendTauriNotification({ title: 'only-title' });
    assert.deepEqual(calls[0].payload.options, { title: 'only-title', body: '' });
  });

  test('prefers the plugin JS global when the shell exposes one', async () => {
    const sent = [];
    setTauri({
      notification: {
        sendNotification: (payload) => { sent.push(payload); },
        isPermissionGranted: async () => true,
      },
      core: { invoke: async () => { throw new Error('should not be used'); } },
    });
    sendTauriNotification({ title: 'via-global', body: 'b' });
    assert.equal(sent.length, 1);
    assert.equal(await isTauriPermissionGranted(), true);
  });

  test('permission checks map to their commands', async () => {
    setTauri(invokeApi({
      'plugin:notification|is_permission_granted': true,
      'plugin:notification|request_permission': 'granted',
    }));
    assert.equal(await isTauriPermissionGranted(), true);
    assert.equal(await requestTauriPermission(), 'granted');
    assert.deepEqual(calls.map((c) => c.command), [
      'plugin:notification|is_permission_granted',
      'plugin:notification|request_permission',
    ]);
  });

  test('a non-string permission answer degrades to denied', async () => {
    setTauri(invokeApi({ 'plugin:notification|request_permission': undefined }));
    assert.equal(await requestTauriPermission(), 'denied');
  });

  test('a throwing plugin never propagates to the caller', async () => {
    setTauri({
      core: { invoke: async () => { throw new Error('plugin exploded'); } },
    });
    // Display failure must not take down the notifying component.
    assert.equal(sendTauriNotification({ title: 'x' }), true);
    assert.equal(await isTauriPermissionGranted(), false);
    assert.equal(await requestTauriPermission(), 'denied');
  });
});

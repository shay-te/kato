// Native OS notifications when running inside the Tauri desktop shell.
//
// The web `Notification` API works in a browser (`kato up` + a tab) but is
// INERT inside the desktop app, which is why "approval needed" pings never
// appeared there:
//   - Windows (WebView2): the API exists, so `'Notification' in window` is
//     true and the toggle looks like it worked — but WebView2 drops web
//     notifications unless the host app handles NotificationReceived, which
//     Tauri does not. Silently nothing. This is the reported bug.
//   - macOS (WKWebView): the API isn't implemented at all.
//
// So inside the shell we route through the notification plugin instead
// (registered in src-tauri/src/main.rs, allowed in capabilities/default.json).
// In a plain browser `window.__TAURI__` is absent and every function here is a
// no-op, leaving the existing web path untouched.
//
// KNOWN GAP: the web path wires `notification.onclick` to focus the task tab.
// The plugin's click-through needs an action-type registration + event
// listener, which this does not do — a desktop notification is display-only
// for now.

function tauriApi() {
  return (typeof window !== 'undefined' && window.__TAURI__) || null;
}

export function isTauriShell() {
  return Boolean(tauriApi());
}

// Each call tries the plugin's JS global first, then its low-level command
// name, so it works regardless of which surface the shell exposes (same
// belt-and-braces approach as openExternalUrl in tauriLinks.js). Command
// names + payload shape verified against tauri-plugin-notification 2.3.3
// (src/commands.rs → notify(options: NotificationData{title, body})).
async function callPlugin(globalFn, command, payload) {
  const t = tauriApi();
  if (!t) { return null; }
  try {
    if (t.notification && typeof t.notification[globalFn] === 'function') {
      return await t.notification[globalFn](payload);
    }
    if (t.core && typeof t.core.invoke === 'function') {
      return await t.core.invoke(`plugin:notification|${command}`, payload);
    }
  } catch (_) { /* degrade to "no notification", never throw at a caller */ }
  return null;
}

export async function isTauriPermissionGranted() {
  return Boolean(
    await callPlugin('isPermissionGranted', 'is_permission_granted', undefined),
  );
}

// Returns the permission string the OS reported ('granted' / 'denied').
export async function requestTauriPermission() {
  const result = await callPlugin('requestPermission', 'request_permission', undefined);
  return typeof result === 'string' ? result : 'denied';
}

export function sendTauriNotification({ title, body }) {
  if (!tauriApi()) { return false; }
  // Fire-and-forget: the caller (notify) is synchronous, and a failed
  // notification must never surface as an unhandled rejection.
  callPlugin('sendNotification', 'notify', {
    options: { title: String(title || ''), body: String(body || '') },
  });
  return true;
}

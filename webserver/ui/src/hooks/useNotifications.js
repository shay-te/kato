import { useCallback, useEffect, useRef, useState } from 'react';
import { NOTIFICATION_KIND } from '../constants/notificationKind.js';
import {
  readEnabled,
  readKindPrefs,
  writeEnabled,
  writeKindPrefs,
} from '../utils/notificationsStorage.js';
import {
  isTauriPermissionGranted,
  isTauriShell,
  requestTauriPermission,
  sendTauriNotification,
} from '../utils/tauriNotifications.js';

// Kinds that fire even while the operator is on that very task.
//
// The general rule — do not notify about what you are already looking at —
// assumes the on-screen UI is the better signal. That breaks for a long job
// the operator deliberately walked away from: "update source" across a
// multi-repo task can run for minutes, and a focused window is not evidence
// anyone is watching it.
const ALWAYS_NOTIFY_KINDS = new Set([NOTIFICATION_KIND.SOURCE_UPDATE]);

export function useNotifications({ activeTaskId, onTaskClick }) {
  // Two delivery paths. The web API is the browser one; inside the desktop
  // shell it is inert (WebView2 drops it, WKWebView lacks it — the "desktop
  // notifications don't work on Windows" report), so the shell routes through
  // the native notification plugin instead. See utils/tauriNotifications.js.
  const webSupported = typeof window !== 'undefined' && 'Notification' in window;
  const desktopShell = isTauriShell();
  const supported = webSupported || desktopShell;
  const [permission, setPermission] = useState(
    // The desktop permission is only knowable asynchronously (see the effect
    // below); start pessimistic so nothing fires before the answer lands.
    webSupported && !desktopShell ? Notification.permission : 'denied',
  );
  const [enabled, setEnabled] = useState(() => (
    webSupported && !desktopShell && Notification.permission === 'granted'
    && readEnabled()
  ));
  const [kindPrefs, setKindPrefs] = useState(() => readKindPrefs());
  const onTaskClickRef = useRef(onTaskClick);
  onTaskClickRef.current = onTaskClick;
  const activeTaskIdRef = useRef(activeTaskId);
  activeTaskIdRef.current = activeTaskId;
  const kindPrefsRef = useRef(kindPrefs);
  kindPrefsRef.current = kindPrefs;

  const persistEnabled = useCallback((value) => {
    setEnabled(value);
    writeEnabled(value);
  }, []);

  const setKindEnabled = useCallback((kind, on) => {
    setKindPrefs((prev) => {
      const next = { ...prev, [kind]: !!on };
      writeKindPrefs(next);
      return next;
    });
  }, []);

  // Adopt the OS's answer on the desktop side. Permission there is an async
  // plugin call, so unlike the web path it can't be read during the initial
  // render — which is why ``enabled`` is re-derived here rather than seeded
  // in useState.
  useEffect(() => {
    if (!desktopShell) { return undefined; }
    let cancelled = false;
    isTauriPermissionGranted().then((granted) => {
      if (cancelled) { return; }
      setPermission(granted ? 'granted' : 'default');
      if (granted && readEnabled()) { setEnabled(true); }
    });
    return () => { cancelled = true; };
  }, [desktopShell]);

  const toggle = useCallback(async () => {
    if (!supported) { return; }
    if (enabled) { persistEnabled(false); return; }
    if (desktopShell) {
      const granted = (await isTauriPermissionGranted())
        || (await requestTauriPermission()) === 'granted';
      setPermission(granted ? 'granted' : 'denied');
      if (!granted) { return; }
      persistEnabled(true);
      return;
    }
    if (Notification.permission === 'denied') { return; }
    if (Notification.permission === 'default') {
      const result = await Notification.requestPermission();
      setPermission(result);
      if (result !== 'granted') { return; }
    }
    persistEnabled(true);
  }, [desktopShell, enabled, persistEnabled, supported]);

  const notify = useCallback(({ title, body, taskId, kind }) => {
    if (!enabled || !supported) { return; }
    // Only the web path can consult Notification.permission synchronously;
    // on the desktop the granted-check already gated ``enabled`` above.
    if (!desktopShell && Notification.permission !== 'granted') { return; }
    // Suppressed for the task you are LOOKING at — its own UI already told
    // you. Except for kinds that report a long job finishing: those exist
    // precisely so the operator can start one and stop watching, and the
    // window being focused says nothing about whether they are still there.
    const alwaysNotify = ALWAYS_NOTIFY_KINDS.has(kind);
    if (!alwaysNotify
        && !document.hidden && taskId && taskId === activeTaskIdRef.current) {
      return;
    }
    // Per-kind opt-out. Unknown kinds are allowed by default so a new
    // notification surface doesn't get silently swallowed.
    const kindKey = kind || 'info';
    if (kindPrefsRef.current[kindKey] === false) { return; }
    if (desktopShell) {
      sendTauriNotification({ title, body: body || '' });
      return;
    }
    try {
      const notification = new Notification(title, {
        body: body || '',
        icon: '/logo.png',
        tag: `kato-${kindKey}-${taskId || 'global'}`,
      });
      notification.onclick = () => {
        window.focus();
        if (taskId && typeof onTaskClickRef.current === 'function') {
          onTaskClickRef.current(taskId);
        }
        notification.close();
      };
    } catch (_) { /* stricter browser policies — degrade silently */ }
  }, [desktopShell, enabled, supported]);

  useEffect(() => {
    // Web-only: polls for a permission revoked in browser settings. Guarded on
    // ``webSupported`` (not ``supported``) so the desktop shell, where
    // ``Notification`` may not exist at all, never dereferences it.
    if (!webSupported || desktopShell) { return; }
    const id = setInterval(() => {
      if (Notification.permission !== permission) {
        setPermission(Notification.permission);
        if (Notification.permission !== 'granted' && enabled) {
          persistEnabled(false);
        }
      }
    }, 5000);
    return () => clearInterval(id);
  }, [desktopShell, enabled, permission, persistEnabled, webSupported]);

  return {
    supported,
    enabled,
    permission,
    toggle,
    notify,
    kindPrefs,
    setKindEnabled,
  };
}

import { useCallback, useEffect, useRef, useState } from 'react';
import { NOTIFICATION_KIND } from '../constants/notificationKind.js';
import { cssEscapeAttr } from '../utils/dom.js';

const STORAGE_KEY = 'kato.notifications';
const KIND_STORAGE_KEY = 'kato.notifications.kinds';
const ALL_KINDS = Object.values(NOTIFICATION_KIND);

// Sensible defaults: notify only on actionable events (task start, end,
// approval needed, errors). The chatty kinds (every Claude reply, every
// platform-state transition) are off by default — they spam the bell
// during normal task flow.
const DEFAULT_KIND_PREFS = {
  [NOTIFICATION_KIND.STARTED]: true,
  [NOTIFICATION_KIND.STATUS_CHANGE]: false,
  [NOTIFICATION_KIND.COMPLETED]: true,
  [NOTIFICATION_KIND.ATTENTION]: true,
  [NOTIFICATION_KIND.ERROR]: true,
  [NOTIFICATION_KIND.REPLY]: false,
};

function _defaultKindPrefs() {
  return { ...DEFAULT_KIND_PREFS };
}

function _readKindPrefs() {
  if (typeof localStorage === 'undefined') {
    return _defaultKindPrefs();
  }
  try {
    const raw = localStorage.getItem(KIND_STORAGE_KEY);
    if (!raw) {
      return _defaultKindPrefs();
    }
    const parsed = JSON.parse(raw);
    // Operator's stored prefs take priority; fall back to default for any
    // kind they haven't explicitly set yet.
    return Object.fromEntries(
      ALL_KINDS.map((k) => [
        k,
        parsed[k] !== undefined ? parsed[k] !== false : DEFAULT_KIND_PREFS[k] !== false,
      ]),
    );
  } catch (_) {
    return _defaultKindPrefs();
  }
}

export function useNotifications({ activeTaskId, onTaskClick }) {
  const supported = typeof window !== 'undefined' && 'Notification' in window;
  const [permission, setPermission] = useState(
    supported ? Notification.permission : 'denied',
  );
  const [enabled, setEnabled] = useState(() => (
    supported
    && permission === 'granted'
    && (typeof localStorage !== 'undefined'
        && localStorage.getItem(STORAGE_KEY) === 'on')
  ));
  const [kindPrefs, setKindPrefs] = useState(_readKindPrefs);
  const onTaskClickRef = useRef(onTaskClick);
  onTaskClickRef.current = onTaskClick;
  const activeTaskIdRef = useRef(activeTaskId);
  activeTaskIdRef.current = activeTaskId;
  const kindPrefsRef = useRef(kindPrefs);
  kindPrefsRef.current = kindPrefs;

  const persistEnabled = useCallback((value) => {
    setEnabled(value);
    try { localStorage.setItem(STORAGE_KEY, value ? 'on' : 'off'); }
    catch (_) { /* private mode / quota */ }
  }, []);

  const setKindEnabled = useCallback((kind, on) => {
    setKindPrefs((prev) => {
      const next = { ...prev, [kind]: !!on };
      try { localStorage.setItem(KIND_STORAGE_KEY, JSON.stringify(next)); }
      catch (_) { /* private mode / quota */ }
      return next;
    });
  }, []);

  const toggle = useCallback(async () => {
    if (!supported) { return; }
    if (enabled) { persistEnabled(false); return; }
    if (Notification.permission === 'denied') { return; }
    if (Notification.permission === 'default') {
      const result = await Notification.requestPermission();
      setPermission(result);
      if (result !== 'granted') { return; }
    }
    persistEnabled(true);
  }, [enabled, persistEnabled, supported]);

  const notify = useCallback(({ title, body, taskId, kind }) => {
    if (!enabled || !supported || Notification.permission !== 'granted') { return; }
    if (!document.hidden && taskId && taskId === activeTaskIdRef.current) { return; }
    // Per-kind opt-out. Unknown kinds are allowed by default so a new
    // notification surface doesn't get silently swallowed.
    const kindKey = kind || 'info';
    if (kindPrefsRef.current[kindKey] === false) { return; }
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
  }, [enabled, supported]);

  useEffect(() => {
    if (!supported) { return; }
    const id = setInterval(() => {
      if (Notification.permission !== permission) {
        setPermission(Notification.permission);
        if (Notification.permission !== 'granted' && enabled) {
          persistEnabled(false);
        }
      }
    }, 5000);
    return () => clearInterval(id);
  }, [enabled, permission, persistEnabled, supported]);

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

export { cssEscapeAttr };

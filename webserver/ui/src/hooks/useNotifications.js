import { useCallback, useEffect, useRef, useState } from 'react';
import { cssEscapeAttr } from '../utils/dom.js';

const STORAGE_KEY = 'kato.notifications';

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
  const onTaskClickRef = useRef(onTaskClick);
  onTaskClickRef.current = onTaskClick;
  const activeTaskIdRef = useRef(activeTaskId);
  activeTaskIdRef.current = activeTaskId;

  const persistEnabled = useCallback((value) => {
    setEnabled(value);
    try { localStorage.setItem(STORAGE_KEY, value ? 'on' : 'off'); }
    catch (_) { /* private mode / quota */ }
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
    try {
      const notification = new Notification(title, {
        body: body || '',
        icon: '/logo.png',
        tag: `kato-${kind || 'info'}-${taskId || 'global'}`,
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

  return { supported, enabled, permission, toggle, notify };
}

export { cssEscapeAttr };

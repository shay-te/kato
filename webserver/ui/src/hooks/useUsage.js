import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchUsage } from '../api.js';

// Per-task token usage for the Usage dialog.
//
// Read once when the dialog opens and again on request — never polled. The
// numbers come from walking the agent's transcripts, which can be hundreds of
// megabytes; nothing on the page needs them to be live, and a poll would make
// the host re-read them every few seconds for a dialog that is closed.
//
// ``status`` is 'loading' until the first answer, then 'ready' or 'error'. A
// reload keeps showing the previous figures ('refreshing') rather than blanking
// the table, so pressing Refresh never makes the list jump.
export function useUsage() {
  const [state, setState] = useState({ status: 'loading', data: null, error: '' });
  // Only the newest request may write: a slow earlier answer arriving after a
  // later one would put older numbers on screen.
  const latest = useRef(0);
  const mounted = useRef(true);

  const load = useCallback(async () => {
    const mine = latest.current + 1;
    latest.current = mine;
    setState((prev) => ({ ...prev, status: prev.data ? 'refreshing' : 'loading', error: '' }));
    try {
      const data = await fetchUsage();
      if (mounted.current && latest.current === mine) {
        setState({ status: 'ready', data, error: '' });
      }
    } catch (err) {
      if (mounted.current && latest.current === mine) {
        setState((prev) => ({ ...prev, status: 'error', error: String(err?.message || err) }));
      }
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    load();
    return () => { mounted.current = false; };
  }, [load]);

  return { ...state, reload: load };
}

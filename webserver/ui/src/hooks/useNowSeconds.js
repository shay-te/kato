import { useEffect, useState } from 'react';

// The current time in seconds, re-rendering once a second while ``active``.
//
// For "fixing · 4m 12s" readouts: the elapsed time moves every second while
// something runs, and nothing needs to re-render once it stops — so the timer
// only exists while ``active`` is true.
export function useNowSeconds(active, intervalMs = 1000) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!active) { return undefined; }
    setNow(Date.now() / 1000);
    const timer = window.setInterval(() => { setNow(Date.now() / 1000); }, intervalMs);
    return () => { window.clearInterval(timer); };
  }, [active, intervalMs]);
  return now;
}

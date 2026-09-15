// A flag that turns on only once its condition has held for ``delayMs``.
//
// For loading indicators. When the data usually arrives within a few frames —
// restored from browser storage on a reload, retained in memory on a switch —
// showing a loader immediately paints it for those frames and removes it
// again: a flash of loading state the operator reads as flicker ("super fast
// blinking of the tree moving from loading state to loaded state"). Waiting
// out a short grace period means a fast load never shows a loader at all, and
// a genuinely slow one still does.
//
// Drops to ``false`` in the SAME render the condition clears, so a loader can
// linger after its data has arrived for no longer than that render.

import { useEffect, useState } from 'react';

export function useDelayedFlag(active, delayMs) {
  const [elapsed, setElapsed] = useState(false);
  useEffect(() => {
    if (!active) {
      setElapsed(false);
      return undefined;
    }
    const handle = setTimeout(
      () => setElapsed(true),
      Math.max(0, Number(delayMs) || 0),
    );
    return () => clearTimeout(handle);
  }, [active, delayMs]);
  return Boolean(active) && elapsed;
}

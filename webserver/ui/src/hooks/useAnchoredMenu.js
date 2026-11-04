import { useCallback, useEffect, useState } from 'react';
import { useClampedPointMenu } from './useClampedPointMenu.js';

// A small menu that opens under the control that was clicked: placed from
// that control's rect (it is portalled out of whatever would clip it — a
// sideways-scrolling strip, a header that truncates), kept inside the
// viewport, closed by Escape. ``toggle(event)`` is the control's onClick.
export function useAnchoredMenu() {
  const [anchor, setAnchor] = useState(null);
  const { menuRef, style } = useClampedPointMenu(anchor);
  const open = anchor !== null;
  const close = useCallback(() => setAnchor(null), []);

  useEffect(() => {
    if (!open) { return undefined; }
    const onKey = (event) => { if (event.key === 'Escape') { setAnchor(null); } };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  function toggle(event) {
    if (open) { close(); return; }
    // Read the rect now: ``currentTarget`` is gone once the event ends.
    const rect = event.currentTarget.getBoundingClientRect();
    setAnchor({ x: rect.left, y: rect.bottom + 4 });
  }

  return { open, toggle, close, menuRef, style };
}

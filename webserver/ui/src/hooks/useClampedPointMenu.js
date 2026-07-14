import { useLayoutEffect, useRef, useState } from 'react';

// Positions a `position: fixed` popup menu anchored at a click point
// (event.clientX/clientY) so it never renders past the viewport edge.
// Right-clicking near the bottom of a long file/tree previously opened
// the menu downward from the click point with no bounds check, pushing
// its lower items below the fold where they couldn't be clicked.
//
// Measures the menu AFTER it renders (its height depends on how many
// items are enabled) via useLayoutEffect, so there's no one-frame flash
// at the wrong position — mirrors TabTooltip's measure-then-place
// technique, simplified for a point anchor instead of a trigger rect.
const VIEWPORT_PAD = 8;

export function useClampedPointMenu(anchor) {
  const menuRef = useRef(null);
  const [pos, setPos] = useState(null);

  useLayoutEffect(() => {
    if (!anchor || !menuRef.current) {
      setPos(null);
      return;
    }
    const box = menuRef.current.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    // Flip upward from the click point if it would overflow the bottom
    // edge — the click point itself stays visible either way.
    let top = anchor.y;
    if (top + box.height + VIEWPORT_PAD > vh) {
      top = anchor.y - box.height;
    }
    top = Math.max(VIEWPORT_PAD, top);

    let left = anchor.x;
    if (left + box.width + VIEWPORT_PAD > vw) {
      left = vw - box.width - VIEWPORT_PAD;
    }
    left = Math.max(VIEWPORT_PAD, left);

    setPos({ top, left });
  }, [anchor]);

  const style = pos
    ? { left: `${pos.left}px`, top: `${pos.top}px` }
    : { left: '-9999px', top: '-9999px' };

  return { menuRef, style };
}

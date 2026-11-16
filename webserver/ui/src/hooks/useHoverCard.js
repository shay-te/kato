import { useEffect, useRef, useState } from 'react';

// Delay before a hover card appears — long enough that running the pointer
// across a row of tabs doesn't flash a card on every one it passes.
export const HOVER_CARD_DELAY_MS = 350;

// A hover card's open/close: ``ref`` goes on the trigger; ``anchorRect`` is a
// frozen snapshot of its viewport rect, taken when the card opens (TabTooltip
// places itself off it). The tasks menu rows and a review loop's tab share it.
export function useHoverCard(delayMs = HOVER_CARD_DELAY_MS) {
  const ref = useRef(null);
  const timerRef = useRef(null);
  const [anchorRect, setAnchorRect] = useState(null);
  useEffect(() => () => clearTimeout(timerRef.current), []);
  function open() {
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      if (ref.current) { setAnchorRect(ref.current.getBoundingClientRect()); }
    }, delayMs);
  }
  function close() {
    clearTimeout(timerRef.current);
    setAnchorRect(null);
  }
  return { ref, anchorRect, open, close };
}

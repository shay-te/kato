import { useEffect, useRef } from 'react';
import { useDismissOnOutsidePointerOrEscape } from './useDismissOnOutsidePointerOrEscape.js';
import { useHoverExpanded } from '../stores/hoverExpand.js';

// Everything a hover-to-expand panel needs, ONCE — the tasks menu and the
// files rail beside a maximized pane both use this and nothing else:
//   - open / close on pointer enter / leave, with the shared timing
//     (``createHoverExpand``);
//   - close on Escape or a press outside it (a touch screen has no pointer to
//     leave with) — a press inside, the panel's own handle included, does not;
//   - ``enabled`` false (the files tree back to a plain column): no hover,
//     and the panel collapses, so it starts collapsed next time.
//
// Returns ``{ ref, open, onMouseEnter, onMouseLeave }`` for the panel's root.
export function useHoverExpandPanel(panel, { enabled = true } = {}) {
  const ref = useRef(null);
  const open = useHoverExpanded(panel) && enabled;
  useEffect(() => { if (!enabled) { panel.close(); } }, [enabled, panel]);
  useDismissOnOutsidePointerOrEscape(open, panel.close, ref);
  return {
    ref,
    open,
    onMouseEnter: enabled ? panel.openSoon : undefined,
    onMouseLeave: enabled ? panel.closeSoon : undefined,
  };
}

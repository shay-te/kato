import { useEffect } from 'react';
import { modalOrDrawerOpen } from '../utils/modalOpen.js';

/**
 * Tab / Shift+Tab cycles the task navigation strip at the top.
 *
 *   Tab        → next task    (wraps from last → first)
 *   Shift+Tab  → previous task (wraps from first → last)
 *
 * Tab is also the browser's focus-traversal key, so we hand it back
 * to the browser whenever the operator is plausibly using it for
 * that instead:
 *
 *  - focus is in an editable field (chat composer, search box, any
 *    settings input) — typing / field tabbing must keep working;
 *  - a modal or the settings drawer is open — Tab should traverse
 *    focus *within* that surface, and silently swapping the task
 *    underneath an open dialog would be disorienting.
 *
 * Only a bare Tab / Shift+Tab is claimed; any Ctrl/Cmd/Alt
 * combination is left alone so it can't shadow OS / browser
 * shortcuts.
 */
function isEditableTarget(el) {
  if (!el) { return false; }
  const tag = el.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') {
    return true;
  }
  return !!el.isContentEditable;
}

export function useTaskTabShortcuts({
  sessions, activeTaskId, onSelect, visibleOrder,
}) {
  useEffect(() => {
    function onKeyDown(event) {
      if (event.key !== 'Tab') { return; }
      if (event.ctrlKey || event.metaKey || event.altKey) { return; }
      if (isEditableTarget(event.target)
          || isEditableTarget(document.activeElement)) {
        return;
      }
      if (modalOrDrawerOpen()) { return; }

      // The strip's VISIBLE order when the tab strip has reported one.
      // ``sessions`` is the raw, unsorted list: cycling by it walked the
      // pre-drag order while the strip showed the operator's arrangement,
      // so Tab jumped somewhere other than the tab sitting next to the
      // current one. Falls back to ``sessions`` before the strip has
      // mounted or reported.
      const ids = (visibleOrder && visibleOrder.length)
        ? visibleOrder
        : (sessions || []).map((s) => s.task_id);
      if (ids.length === 0) { return; }

      // We're taking over Tab — stop the browser from also moving
      // DOM focus, which would fight the task switch.
      event.preventDefault();

      const back = event.shiftKey;
      const current = ids.indexOf(activeTaskId);
      let nextIndex;
      if (current === -1) {
        // Nothing selected yet: Tab → first, Shift+Tab → last.
        nextIndex = back ? ids.length - 1 : 0;
      } else {
        nextIndex = (current + (back ? -1 : 1) + ids.length) % ids.length;
      }
      const nextId = ids[nextIndex];
      if (nextId && nextId !== activeTaskId) {
        onSelect(nextId);
      }
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [sessions, visibleOrder, activeTaskId, onSelect]);
}

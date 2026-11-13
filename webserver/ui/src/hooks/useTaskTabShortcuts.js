import { useEffect, useRef } from 'react';
import { createKeyHold } from '../utils/keyHold.js';
import { modalOrDrawerOpen } from '../utils/modalOpen.js';
import { TASK_SWITCHER_REVEAL_MS, setTaskSwitcherShown } from '../stores/taskSwitchStore.js';

/**
 * Tab / Shift+Tab — and Ctrl+Tab / Ctrl+Shift+Tab — cycle the tasks, in the
 * tasks menu's order, whether the menu is open or not.
 *
 *   Tab, Ctrl+Tab              → next task    (wraps from last → first)
 *   Shift+Tab, Ctrl+Shift+Tab  → previous task (wraps from first → last)
 *
 * Ctrl+Tab is the browser's own "next browser tab" in a plain browser, which
 * keeps it for itself — the page never sees it there. The desktop app has no
 * browser tabs, so it arrives and switches tasks like Tab.
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
 * Only Tab with no modifier or with Ctrl alone is claimed (Shift picks the
 * direction); Cmd / Alt combinations are left alone so they can't shadow OS
 * shortcuts (macOS keeps Cmd+Tab for itself anyway).
 *
 * A quick press only switches. The task switcher overlay shows only while the
 * press is HELD past ``TASK_SWITCHER_REVEAL_MS`` — Tab held down, or Ctrl kept
 * down over Ctrl+Tab — and goes when it is let go (``utils/keyHold.js``).
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
  // One hold for the page's life: the keydown listener below is re-added on
  // every switch (its deps change), and a hold must outlive that.
  const holdRef = useRef(null);
  if (holdRef.current === null) {
    holdRef.current = createKeyHold({
      thresholdMs: TASK_SWITCHER_REVEAL_MS,
      onHold: () => setTaskSwitcherShown(true),
      onRelease: () => setTaskSwitcherShown(false),
    });
  }

  useEffect(() => {
    const hold = holdRef.current;
    const onKeyUp = (event) => hold.keyUp(event.key);
    const onBlur = () => hold.cancel();
    window.addEventListener('keyup', onKeyUp, true);
    window.addEventListener('blur', onBlur);
    return () => {
      window.removeEventListener('keyup', onKeyUp, true);
      window.removeEventListener('blur', onBlur);
      hold.cancel();
    };
  }, []);

  useEffect(() => {
    function onKeyDown(event) {
      if (event.key !== 'Tab') { return; }
      if (event.metaKey || event.altKey) { return; }
      // Ctrl+Tab is unambiguous — nobody moves focus with it — so it switches
      // tasks even from inside a text field. A bare Tab there stays the
      // field's own (focus traversal, indentation).
      if (!event.ctrlKey && (isEditableTarget(event.target)
          || isEditableTarget(document.activeElement))) {
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
      // Held, this press reveals the switcher; Ctrl+Tab's hold lasts while
      // Ctrl does, so Tab can be tapped on through the tasks.
      holdRef.current.press(event.ctrlKey ? 'Control' : 'Tab');

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

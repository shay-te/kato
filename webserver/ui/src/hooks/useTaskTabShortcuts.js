import { useEffect } from 'react';

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

function modalOrDrawerOpen() {
  if (typeof document === 'undefined') { return false; }
  return !!document.querySelector(
    '[role="dialog"][aria-modal="true"], .settings-drawer.is-open',
  );
}

export function useTaskTabShortcuts({ sessions, activeTaskId, onSelect }) {
  useEffect(() => {
    function onKeyDown(event) {
      if (event.key !== 'Tab') { return; }
      if (event.ctrlKey || event.metaKey || event.altKey) { return; }
      if (isEditableTarget(event.target)
          || isEditableTarget(document.activeElement)) {
        return;
      }
      if (modalOrDrawerOpen()) { return; }

      const ids = (sessions || []).map((s) => s.task_id);
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
  }, [sessions, activeTaskId, onSelect]);
}

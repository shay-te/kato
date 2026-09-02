// Drag-to-reorder for a tab strip, shared by the FILE tabs inside a task and
// the TASK tabs across the top.
//
// Both strips have the same rule: pinned tabs are a block at the front, and a
// tab can only be dropped among its own group. The rule is ENFORCED here, not
// re-applied afterwards — a drop that would put an unpinned tab among the
// pins (or the reverse) is REFUSED and the list comes back unchanged.
//
// Refusing rather than silently correcting is the deliberate half. Snapping
// the tab somewhere the operator did not aim reads as a broken drag; leaving
// it where it was reads as "that isn't allowed", and they can pin it first if
// that is what they meant.
//
// Lives in its own module rather than in ``fileTabs.js`` because the task
// strip needs the identical rule, and a second copy of "what a legal drop
// is" is exactly the kind of thing that drifts until the two strips behave
// differently for no reason anyone can explain.

const DEFAULT_KEY_OF = (tab) => tab && tab.key;
const DEFAULT_PINNED_OF = (tab) => !!(tab && tab.pinned);

// Move the tab identified by ``fromKey`` so it lands at ``toKey``'s position.
//
// ``keyOf`` / ``pinnedOf`` let a caller work with its own item shape — the
// task strip keys on ``task_id`` and reads pinned state from a separate set —
// so neither strip has to reshape its data just to reorder it.
export function moveTab(tabs, fromKey, toKey, options = {}) {
  const keyOf = options.keyOf || DEFAULT_KEY_OF;
  const pinnedOf = options.pinnedOf || DEFAULT_PINNED_OF;
  const list = Array.isArray(tabs) ? tabs : [];
  if (!fromKey || !toKey || fromKey === toKey) { return list; }
  const from = list.findIndex((tab) => keyOf(tab) === fromKey);
  const to = list.findIndex((tab) => keyOf(tab) === toKey);
  if (from < 0 || to < 0) { return list; }
  if (!!pinnedOf(list[from]) !== !!pinnedOf(list[to])) { return list; }
  const next = [...list];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

// May ``fromKey`` be dropped onto ``toKey``?
//
// The strips ask this per rendered tab to decide whether to show a drop
// affordance and whether to let the browser complete the drag at all — the
// cursor has to say "no" over an illegal target rather than letting the
// operator finish a drag that will be refused.
export function canDropOn(dragged, target, pinnedOf = DEFAULT_PINNED_OF) {
  if (!dragged || !target) { return false; }
  if (dragged === target) { return false; }
  return !!pinnedOf(dragged) === !!pinnedOf(target);
}

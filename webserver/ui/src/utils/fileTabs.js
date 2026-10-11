// Pure, storage-agnostic helpers for the VS Code-style multi-file tab
// strip in the centre pane. Every "open a file" action either FOCUSES
// an already-open tab (same repo + path) or APPENDS a brand new tab —
// it never replaces another tab's content, unlike the single-openFile
// model this replaced.
//
// A "tab" is the same shape App.jsx already builds for its (former)
// single ``openFile`` state — ``{ key, taskId, absolutePath,
// relativePath, repoId, view, focusComment, kind, openRequestId,
// editorViewState, diffScrollTop }`` — plus a stable ``key`` used for
// React list rendering and lookups. ``view`` toggles ('file' <-> 'diff')
// happen IN PLACE on the same tab; they don't open a second tab for
// the same file.
//
// No React here — every function takes plain arrays/objects and
// returns new ones, so this is testable without jsdom (same pattern
// as pinnedTabs.js).

// A file's stable identity is its absolute workspace path — the ONE field
// EVERY opener supplies (``handleOpenFile`` requires it). Keying on it means
// the file tree, content search, the diff pane, AND the chat event-log
// "reveal" button (which only knows the absolute path, not repoId /
// relativePath) all resolve to the SAME tab instead of opening a duplicate
// for one physical file. repoId / relativePath stay ON the tab (comment
// lookup + header) but no longer fork the key. Trailing separators are
// trimmed so ``…/repo`` and ``…/repo/`` don't split.
export function tabKeyFor(info) {
  const absolutePath = String((info && info.absolutePath) || '')
    .trim().replace(/[/\\]+$/, '');
  if (absolutePath) { return absolutePath; }
  // Defensive fallback for a malformed open with no absolute path (should
  // not happen — handleOpenFile requires absolutePath).
  const repoId = String(info && info.repoId || '').trim();
  const path = String((info && info.relativePath) || '').trim();
  return repoId + '::' + path;
}

// Insert or focus a tab for ``info`` (the raw request shape passed to
// ``onOpenFile`` — absolutePath/relativePath/repoId/view/focusComment/
// kind). ``openRequestId`` is stamped by the caller (App owns the
// monotonic counter) so repeat-opening the SAME file still bumps a
// value EditorPane/DiffPane can key a re-focus effect off of.
//
// Returns ``{ tabs, activeKey }``:
//   - Existing tab for this repo+path: merged in place (view/
//     focusComment/kind/openRequestId updated; any remembered
//     editorViewState/diffScrollTop is PRESERVED, not wiped, so
//     re-clicking a file you already have open doesn't lose your
//     scroll position) and made active. Tab position doesn't change.
//   - New file: inserted immediately after the current active tab
//     (or appended if there is no active tab / it's not found) and
//     made active.
// Which view a tab should show after an open: stated > remembered > default.
export function pickTabView(requested, existing) {
  if (requested === 'diff' || requested === 'file') { return requested; }
  const remembered = existing && existing.view;
  return remembered === 'diff' || remembered === 'file' ? remembered : 'file';
}


export function upsertTab(tabs, activeKey, info, taskId) {
  const list = Array.isArray(tabs) ? tabs : [];
  const key = tabKeyFor(info);
  const existingIndex = list.findIndex((tab) => tab.key === key);
  const existing = existingIndex >= 0 ? list[existingIndex] : null;
  // When an opener omits repoId / relativePath (the event-log "reveal" button
  // only knows the absolute path), keep the values an earlier FULL open (file
  // tree / content search) already recorded rather than clobbering them with
  // the absolute-path fallback — so re-revealing a file never DEGRADES a good
  // tab's comment lookup / header down to the raw absolute path.
  const repoId = String(info.repoId || (existing && existing.repoId) || '');
  const relativePath = String(
    info.relativePath || (existing && existing.relativePath) || info.absolutePath || '',
  );
  const patch = {
    key,
    taskId,
    absolutePath: info.absolutePath,
    relativePath,
    repoId,
    // AN OPENER THAT SAYS NOTHING INHERITS THE TAB'S OWN VIEW.
    //
    // This was ``info.view === 'diff' ? 'diff' : 'file'``, so any re-open
    // that did not restate the view forced the file back to whole-file —
    // and the file tree, the content search and the reveal button all open
    // without stating one. Put a file in diff view, click another file, come
    // back to the first: it was showing the whole file again. "switching the
    // files, he forget the last state, what is showing."
    //
    // Same precedence the ``repoId``/``relativePath`` lines above already
    // use: an EXPLICIT value wins, otherwise keep what the tab has, and only
    // fall back to the default for a genuinely new tab. The view toggle still
    // works because it always states the view it wants.
    view: pickTabView(info.view, existing),
    focusComment: !!info.focusComment,
    kind: String(info.kind || ''),
    // The line to jump to, when the opener knows one (a content-search hit,
    // a go-to-definition). The tab patch didn't carry it, so the content
    // search's ``line`` — which it has always sent — was dropped here and the
    // editor opened the right file at the top: "it opens the file but won't
    // scroll to the line I clicked". 0 means "no particular line".
    line: Number(info.line) > 0 ? Number(info.line) : 0,
    openRequestId: info.openRequestId,
    // An explicit open/focus is NEVER a task-switch restore. Clear the
    // one-shot ``restoreViewState`` flag (stamped on every tab when a task's
    // saved tab-set is restored) so it can't linger on a tab and later
    // suppress DiffPane's scroll-to-comment when the operator clicks a
    // comment badge on an already-open file (it merges in place and would
    // otherwise keep the stale ``true``).
    restoreViewState: false,
  };
  if (existingIndex >= 0) {
    const next = [...list];
    next[existingIndex] = { ...next[existingIndex], ...patch };
    return { tabs: next, activeKey: key };
  }
  const activeIndex = list.findIndex((tab) => tab.key === activeKey);
  const insertAt = activeIndex >= 0 ? activeIndex + 1 : list.length;
  const next = [...list];
  next.splice(insertAt, 0, patch);
  return { tabs: next, activeKey: key };
}

// Close the tab at ``key``. If it was the active tab, activate its
// left neighbor (or the new first tab if it was leftmost); otherwise
// the active tab is unaffected. Returns ``{ tabs, activeKey }`` —
// ``activeKey`` is ``null`` once the last tab closes.
export function closeTab(tabs, activeKey, key) {
  const list = Array.isArray(tabs) ? tabs : [];
  const index = list.findIndex((tab) => tab.key === key);
  if (index < 0) { return { tabs: list, activeKey }; }
  const next = [...list.slice(0, index), ...list.slice(index + 1)];
  if (key !== activeKey) {
    return { tabs: next, activeKey };
  }
  if (next.length === 0) { return { tabs: next, activeKey: null }; }
  const fallbackIndex = Math.max(0, index - 1);
  return { tabs: next, activeKey: next[fallbackIndex].key };
}

// --- the shared strip ------------------------------------------------------
//
// File tabs share the centre strip with the review-loop tabs and the "New
// task" draft's tab, and it is ONE row of tabs: a tab opens right after the
// one on show, and closing the one on show shows its neighbour — whatever
// kind either is. A file opened while a loop (or the draft) shows records
// that tab's strip id in ``after`` and sits right after it; the loops and
// the draft keep their own order.
export const STRIP_TAB_KIND = Object.freeze({
  FILE: 'file', REVIEW_LOOP: 'review_loop', NEW_TASK: 'new_task',
});

export const NEW_TASK_STRIP_ID = 'new-task';

export function loopStripId(taskId) {
  return `loop:${taskId}`;
}

// The strip in display order, as ``{ kind, id, taskId? }`` entries (a file's
// ``id`` is its tab key): the files that follow no other tab, then each loop
// tab and then the draft's, each followed by the files opened from it. A file
// whose tab has closed since rejoins the first group, in list order.
export function stripOrder(fileTabs, loopTaskIds = [], newTaskOpen = false) {
  const files = Array.isArray(fileTabs) ? fileTabs : [];
  const others = [
    ...loopTaskIds.map((taskId) => ({
      kind: STRIP_TAB_KIND.REVIEW_LOOP, id: loopStripId(taskId), taskId,
    })),
    ...(newTaskOpen ? [{ kind: STRIP_TAB_KIND.NEW_TASK, id: NEW_TASK_STRIP_ID }] : []),
  ];
  const open = new Set(others.map((entry) => entry.id));
  const following = (id) => files
    .filter((tab) => (open.has(tab.after) ? tab.after : '') === id)
    .map((tab) => ({ kind: STRIP_TAB_KIND.FILE, id: tab.key }));
  return [...following(''), ...others.flatMap((entry) => [entry, ...following(entry.id)])];
}

// What shows once ``closedIds`` close while ``shownId`` was on show: its
// nearest open neighbour in ``order`` — the left one first, as a file tab's
// close always picked. null when nothing is left.
export function stripNeighbour(order, shownId, closedIds) {
  const index = order.findIndex((entry) => entry.id === shownId);
  if (index < 0) { return null; }
  const closed = new Set(closedIds);
  const left = order.slice(0, index).reverse().find((entry) => !closed.has(entry.id));
  return left || order.slice(index + 1).find((entry) => !closed.has(entry.id)) || null;
}

// A file tab ``key`` just opened goes right after the tab on show. After a
// file, ``upsertTab`` already put it there, and it joins that file's group
// (``after``). After a loop / the draft (``stripId``), it becomes the first
// of the files that follow that tab.
export function placeOpenedTab(tabs, key, { after = '', stripId = '' } = {}) {
  const list = Array.isArray(tabs) ? tabs : [];
  const opened = list.find((tab) => tab.key === key);
  if (!opened) { return list; }
  if (!stripId) { return list.map((tab) => (tab.key === key ? { ...tab, after } : tab)); }
  const rest = list.filter((tab) => tab.key !== key);
  const first = rest.findIndex((tab) => tab.after === stripId);
  const index = first < 0 ? rest.length : first;
  return [...rest.slice(0, index), { ...opened, after: stripId }, ...rest.slice(index)];
}

// Merge a partial update (Monaco view state, diff scroll offset, ...)
// onto the tab at ``key``. No-op (returns the same array reference)
// when the key isn't found, so callers can call this unconditionally
// without a guard.
export function patchTab(tabs, key, patch) {
  const list = Array.isArray(tabs) ? tabs : [];
  const index = list.findIndex((tab) => tab.key === key);
  if (index < 0) { return list; }
  const next = [...list];
  next[index] = { ...next[index], ...patch };
  return next;
}

export function findTab(tabs, key) {
  return (Array.isArray(tabs) ? tabs : []).find((tab) => tab.key === key) || null;
}


// --- ordering -------------------------------------------------------------
//
// Two rules, in this priority:
//   1. Pinned tabs come first, in their own relative order.
//   2. Within each group, the operator's explicit drag order is kept.
//
// Rule 1 is what makes pinning mean something. A pin that leaves the tab
// wherever it was is just a badge: the reason to pin a file is to stop it
// being pushed off-screen by the next dozen you open, which only works if
// it moves to the front and stays there.

// Stable partition: pinned tabs first, everything else after, each group
// preserving its existing relative order. Stability is the whole contract —
// re-sorting on every render must never reshuffle tabs the operator arranged.
export function sortPinnedFirst(tabs) {
  const list = Array.isArray(tabs) ? tabs : [];
  const pinned = list.filter((tab) => tab && tab.pinned);
  if (pinned.length === 0 || pinned.length === list.length) { return list; }
  return [...pinned, ...list.filter((tab) => !(tab && tab.pinned))];
}

// Pin/unpin ``key`` and re-apply the ordering.
//
// Pinning moves the tab to the END of the pinned group (rightmost pinned)
// rather than the very front: pinning a second file should sit beside the
// first, not leapfrog it. Unpinning drops the tab to the FRONT of the
// unpinned group — directly after the pins — because sending it to the far
// right of a long strip is effectively hiding a tab the operator was just
// looking at.
export function togglePin(tabs, key) {
  const list = Array.isArray(tabs) ? tabs : [];
  const index = list.findIndex((tab) => tab.key === key);
  if (index < 0) { return list; }
  // A pinned tab sits in the block at the front, after no other tab.
  const target = { ...list[index], pinned: !list[index].pinned, after: '' };
  const rest = list.filter((tab) => tab.key !== key);
  const pinned = rest.filter((tab) => tab.pinned);
  const unpinned = rest.filter((tab) => !tab.pinned);
  return target.pinned
    ? [...pinned, target, ...unpinned]
    : [...pinned, target, ...unpinned];
}

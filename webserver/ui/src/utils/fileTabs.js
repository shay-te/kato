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
    view: info.view === 'diff' ? 'diff' : 'file',
    focusComment: !!info.focusComment,
    kind: String(info.kind || ''),
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

// Is a modal dialog or the settings drawer currently open?
//
// Global keyboard shortcuts use this to stand down while a surface that
// owns the keyboard is up: Tab must traverse focus *inside* an open dialog,
// and Escape must close the dialog rather than reach past it to something
// underneath.
//
// Queried from the DOM rather than tracked in a store because the surfaces
// are owned by several unrelated components (permission modal, forget-task
// modal, settings drawer, …) and a shortcut only needs the yes/no answer.
export function modalOrDrawerOpen() {
  if (typeof document === 'undefined') { return false; }
  return !!document.querySelector(
    '[role="dialog"][aria-modal="true"], .settings-drawer.is-open',
  );
}

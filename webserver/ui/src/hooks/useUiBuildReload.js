import { useCallback, useEffect, useState } from 'react';
import { uiBuildStore } from '../stores/uiBuildStore.js';

// Remembers the build this window last reloaded itself for, so a server and
// a page that somehow keep disagreeing cannot turn into a reload loop: one
// automatic reload per build, after which only the button reloads.
const RELOADED_FOR_KEY = 'kato.uiBuild.reloadedFor';

function alreadyReloadedFor(build) {
  try { return window.sessionStorage.getItem(RELOADED_FOR_KEY) === build; }
  catch (_) { return false; }
}

function rememberReloadFor(build) {
  try { window.sessionStorage.setItem(RELOADED_FOR_KEY, build); }
  catch (_) { /* a private window: the loop guard is simply off */ }
}

// Nobody is looking at, or typing into, this window right now.
function unattended() {
  return document.hidden || !document.hasFocus();
}

// Reload a window that is running an older UI bundle than the one on disk —
// without yanking it out from under someone using it.
//
// Unattended (hidden, or another app has the focus): reload at once. That is
// the common case — the operator is in their editor while the bundle is
// rebuilt — and they come back to the new UI without having done anything.
// Attended: do NOT reload; the page they are reading must not jump. Return
// ``stale`` so a banner can offer the reload, and take the first moment they
// look away. Drafts, queued prompts and the open task all survive a reload.
export function useUiBuildReload(reloadPage = () => window.location.reload()) {
  const [target, setTarget] = useState('');
  useEffect(() => uiBuildStore.subscribe(setTarget), []);

  const reload = useCallback(() => {
    if (target) { rememberReloadFor(target); }
    reloadPage();
  }, [target, reloadPage]);

  useEffect(() => {
    if (!target || alreadyReloadedFor(target)) { return undefined; }
    function reloadIfUnattended() {
      if (unattended()) { reload(); }
    }
    reloadIfUnattended();
    document.addEventListener('visibilitychange', reloadIfUnattended);
    window.addEventListener('blur', reloadIfUnattended);
    return () => {
      document.removeEventListener('visibilitychange', reloadIfUnattended);
      window.removeEventListener('blur', reloadIfUnattended);
    };
  }, [target, reload]);

  return { stale: Boolean(target), reload };
}

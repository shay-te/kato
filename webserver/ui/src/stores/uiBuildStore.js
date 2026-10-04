// Is this window still running the UI bundle that is on disk?
//
// The bundle keeps one file name across rebuilds, so a window that is simply
// left open goes on running whatever it loaded — every UI fix needed someone
// to remember to reload before it existed for them. The server now says which
// build is current on every API response (``X-Kato-UI-Build``) and stamps the
// one a page loaded into a <meta>; this store compares the two.
//
// It only decides WHETHER the window is stale. What to do about it — reload
// now, or wait until nobody is looking — is the caller's (useUiBuildReload).
// Plain pub/sub, like agentStatusStore: no React, no context.

import { createPubSub } from './pubsub.js';

export const UI_BUILD_HEADER = 'X-Kato-UI-Build';

// A different build has to be seen CONTINUOUSLY for this long before the
// window counts as stale. A rebuild writes its files seconds apart, so the id
// changes more than once on the way; acting on the first change would reload
// into a half-written bundle and then reload again for the rest of it.
export const STABLE_MS = 10000;

function readLoadedBuild() {
  if (typeof document === 'undefined') { return ''; }
  const meta = document.querySelector('meta[name="kato-ui-build"]');
  return String(meta?.getAttribute('content') || '').trim();
}

let _loaded = readLoadedBuild();
let _candidate = '';
let _candidateSince = 0;
let _latest = '';

const _pubsub = createPubSub(() => _latest);

export const uiBuildStore = {
  // Subscribers receive the build this window should move to — '' while the
  // window is current.
  subscribe: _pubsub.subscribe,

  // Called with the header value of an API response. Cheap and safe to call
  // on every one: nothing happens unless the answer changes.
  observe(build, now = Date.now()) {
    const current = String(build || '').trim();
    // No header (an older server) or no <meta> (a page that predates this):
    // there is nothing to compare, so the window is never called stale.
    if (!current || !_loaded) { return; }
    if (current === _loaded) {
      _candidate = '';
      if (_latest) { _latest = ''; _pubsub.emit(); }
      return;
    }
    if (current !== _candidate) {
      _candidate = current;
      _candidateSince = now;
      return;
    }
    if (current !== _latest && now - _candidateSince >= STABLE_MS) {
      _latest = current;
      _pubsub.emit();
    }
  },

  // The build to move to, or '' when this window is current.
  staleFor() { return _latest; },

  // Test seam: pretend the page loaded ``build``.
  resetForTest(build = '') {
    _loaded = String(build || '');
    _candidate = '';
    _candidateSince = 0;
    _latest = '';
  },
};

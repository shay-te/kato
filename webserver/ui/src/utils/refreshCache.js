// Ask the server to drop a discovery cache, over POST.
//
// These three refreshes used to ride ``?refresh=1`` on their own GET routes.
// Each is an ACTION: it spawns a CLI subprocess, calls the npm registry or the
// models API, and — for the backend probe — clears a cache global to the whole
// server process, so one client's refresh invalidates it for everyone.
//
// Browsers issue GETs nobody asked for (Chrome prefetches links on hover, link
// previewers fetch to render a card), so a verb that promises "this only
// reads" should not be the one that respawns a subprocess.
//
// NOT a security change — every kato route, GET included, already sits behind
// the same origin guard. This is about what can fire by accident.
//
// DELIBERATELY SELF-CONTAINED: the whole client half of the feature is this
// file plus one ``await refreshCache(...)`` line in each of the three api.js
// functions. To remove it, delete this file, drop those three lines, and put
// ``?refresh=1`` back on the GETs.

// Target names — must match webserver/kato_webserver/cache_refresh.py.
export const REFRESH_TARGET = {
  AGENT_VERSION: 'agent-version',
  AGENT_BACKENDS: 'agent-backends',
  MODELS: 'models',
};

// Fire the refresh and resolve when the server has dropped the cache, so the
// caller's follow-up GET cannot race it and re-read the stale entry.
//
// Never throws. A refresh that fails leaves the cache warm, which means the
// caller shows slightly stale data — strictly better than turning a Refresh
// button into an error dialog, and the next attempt is one click away.
export async function refreshCache(target) {
  try {
    await fetch('/api/refresh', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ target }),
    });
  } catch (_err) {
    // Offline / server down. The GET that follows will fail visibly on its
    // own if the server is really gone; there is nothing useful to add here.
  }
}

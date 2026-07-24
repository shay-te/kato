// Open external links in the system browser when running inside the Tauri
// desktop shell.
//
// Claude's markdown output, PR links, docs, etc. render as ordinary
// <a target="_blank"> anchors. In a normal browser (`kato up`) those open a
// new tab as expected. But inside the desktop app's WKWebView a click on such
// a link does NOTHING — the webview won't spawn a browser tab, and navigating
// the webview itself to an external site would replace the whole app. So a
// single delegated click handler (installed ONLY when we detect the Tauri
// shell) intercepts clicks on external http(s) links and hands them to the
// system default browser via Tauri's opener plugin. In a plain browser
// `window.__TAURI__` is absent and this is a no-op — links behave natively.

function tauriApi() {
  return (typeof window !== 'undefined' && window.__TAURI__) || null;
}

// Open a URL in the OS default browser. Tries the opener plugin's JS global
// first, then its low-level command, then the shell plugin as a fallback, so
// it works regardless of which opener surface the shell exposes. Any failure
// is swallowed — a link that can't be opened is left inert, never throws.
export function openExternalUrl(url) {
  const t = tauriApi();
  if (!t) return false;
  try {
    if (t.opener && typeof t.opener.openUrl === 'function') {
      t.opener.openUrl(url);
      return true;
    }
    if (t.core && typeof t.core.invoke === 'function') {
      t.core.invoke('plugin:opener|open_url', { url });
      return true;
    }
    if (t.shell && typeof t.shell.open === 'function') {
      t.shell.open(url);
      return true;
    }
  } catch (_) { /* leave the link inert rather than surface an error */ }
  return false;
}

export function isExternalHttpLink(href) {
  if (!href || !/^https?:\/\//i.test(href)) return false;
  try {
    return new URL(href, window.location.href).host !== window.location.host;
  } catch (_) {
    return false;
  }
}

let _installed = false;

// Test seam: reset the install guard between cases.
export function _resetTauriExternalLinks() {
  _installed = false;
}

export function installTauriExternalLinks(doc = (typeof document !== 'undefined' ? document : null)) {
  if (_installed || !doc) return;
  // Only meaningful inside the Tauri shell. In a browser we leave links alone.
  if (!tauriApi()) return;
  _installed = true;
  doc.addEventListener(
    'click',
    (event) => {
      // Ignore anything the app already handled, or non-primary clicks.
      if (event.defaultPrevented || event.button !== 0) return;
      const target = event.target;
      const anchor = target && target.closest ? target.closest('a[href]') : null;
      if (!anchor) return;
      const href = anchor.getAttribute('href');
      if (!isExternalHttpLink(href)) return;
      if (openExternalUrl(href)) {
        // We handed it to the browser — stop the webview from doing anything
        // with the (dead-in-webview) target="_blank" navigation.
        event.preventDefault();
      }
    },
    true, // capture phase — run before React's own click handlers
  );
}

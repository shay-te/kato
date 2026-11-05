// A rebuild that changed only the styles needs no reload: the page's own two
// stylesheets are pointed at the new files and it looks new at once — nothing
// it holds (a dialog, a half-typed message, the scroll) is touched.
//
// The build id is the three bundle files' stamps, in this order (the server's
// ``_UI_BUILD_FILES``): build/app.js, build/app.css, css/app.css.

const STYLESHEET = /\/(?:build|css)\/app\.css(?:[?#]|$)/;

// Whether ``next`` differs from ``loaded`` in its styles only (the script the
// page runs is the same one).
export function onlyStylesChanged(loaded, next) {
  const before = String(loaded || '').split('-');
  const after = String(next || '').split('-');
  return before.length === 3 && after.length === 3 && before[0] === after[0] && loaded !== next;
}

// Each of the page's app stylesheets, loaded again from ``build``. The new
// sheet goes in next to the old one, and the old one leaves only once the new
// one has loaded, so the page never shows unstyled. Returns how many.
export function swapStylesheets(build, doc = document) {
  const links = [...doc.querySelectorAll('link[rel="stylesheet"]')].filter((link) => STYLESHEET.test(link.getAttribute('href') || ''));
  for (const link of links) {
    const fresh = link.cloneNode();
    const href = (link.getAttribute('href') || '').split(/[?#]/)[0];
    fresh.setAttribute('href', `${href}?v=${encodeURIComponent(build)}`);
    const drop = () => link.remove();
    fresh.addEventListener('load', drop, { once: true });
    fresh.addEventListener('error', drop, { once: true });
    link.after(fresh);
  }
  return links.length;
}

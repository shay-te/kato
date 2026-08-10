import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const css = readFileSync(
  new URL('../../../static/css/app.css', import.meta.url),
  'utf8',
);

function ruleBody(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
  assert.ok(match, `expected ${selector} rule to exist`);
  return match[1];
}

// Reported from a Windows machine: "the dropdown styles are not working at all,
// looks very weird" — the <select> popup rendered as a white listbox with
// near-invisible option text. The popup is painted by the engine, not by our
// CSS, and Chromium (the Windows shell's WebView2) paints it in LIGHT chrome
// unless the page declares its scheme. macOS/WKWebView uses the system
// appearance, which is why it only ever broke on Windows.
test('the page declares a dark colour scheme for native controls', () => {
  assert.match(ruleBody(':root'), /color-scheme\s*:\s*dark\s*;/);
});

// Belt-and-braces for an engine that ignores color-scheme: the option rows
// carry their own OPAQUE fill, because the controls' own background is
// translucent white (rgba(255,255,255,.06)) and composites over whatever
// base the engine picked — white, in the broken case.
test('select options paint their own opaque dark rows', () => {
  const body = ruleBody('select option,\nselect optgroup');
  assert.match(body, /background-color\s*:\s*#303134\s*;/);
  assert.match(body, /color\s*:\s*#f5f5f7\s*;/);
});

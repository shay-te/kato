// Behind a popup the app goes black & white — on the overlay panels only.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// Comments out: only rules are compared.
const css = readFileSync(new URL('../../../static/css/app.css', import.meta.url), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '');

test('both popup overlays grey out what is behind them', () => {
  const rule = css.match(/\.modal:not\(\.is-inline\),\s*\.adopt-session-modal-backdrop\s*\{([^}]*)\}/);
  assert.ok(rule, 'the shared overlay rule is in the built stylesheet');
  assert.match(rule[1], /backdrop-filter:\s*grayscale\(1\)/);
  assert.match(rule[1], /-webkit-backdrop-filter:\s*grayscale\(1\)/);  // the desktop app's WebKit
});

test('nothing else is greyed: not the inline card, not the popup itself', () => {
  const greyed = [...css.matchAll(/([^{}]+)\{[^}]*backdrop-filter:\s*grayscale/g)].map((m) => m[1].trim());
  assert.deepEqual(greyed.map((selector) => selector.replace(/\s+/g, ' ')),
    ['.modal:not(.is-inline), .adopt-session-modal-backdrop']);
});

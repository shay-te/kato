import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const css = readFileSync(
  new URL('../../../static/css/app.css', import.meta.url),
  'utf8',
).replace(/\/\*[\s\S]*?\*\//g, '');

// Icon buttons come in exactly TWO sizes (see constants.scss):
//   LG 28px box / 14px glyph — a standalone button in a toolbar
//   SM 16px box / 10px glyph — a control living inside a pill or row,
//                              which cannot be LG without growing it
//
// Before this there were seven box sizes (16/18/22/26/28/32/34) and nine
// glyph sizes (9→18px), which is what made the toolbars read as
// mismatched. This test is a ratchet: a new icon button must pick one of
// the two scales rather than inventing an eighth.
const BOX = new Set(['28px', '16px']);
const GLYPH = new Set(['14px', '10px']);

// Controls that are legitimately NOT icon buttons, matched by the
// heuristic below but sized by their own concerns.
const EXEMPT = [
  /kato-btn-spinner/,        // 1.05em — tracks its host button's font-size
  /chat-sticky-prompt-toggle/, // full-width row, not a button box
  /settings-drawer-toggle/,  // a switch control
  /settings-drawer-approval-toggle/,
  /tabs-pane:not\(\.tabs-pane-top\)/, // legacy sidebar layout, never rendered
  // Text buttons: their font-size is a LABEL, not a glyph.
  /files-tab-text-btn/, /bubble-tool-details-toggle/, /setup-wizard-btn/,
  /settings-drawer-action/, /settings-drawer-perm-clear/,
  /diff-context-expander-inner/,
];

function rules() {
  return [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => ({
    selector: m[1].trim(),
    body: m[2],
  }));
}

function declaration(body, property) {
  const found = body.match(new RegExp(`(?:^|;)\\s*${property}\\s*:\\s*([^;]+);`));
  return found ? found[1].trim() : null;
}

test('the icon scale tokens are the two documented sizes', () => {
  // Compiled values, so a token edit that breaks the pairing is caught
  // here rather than by eye across a dozen toolbars.
  const shared = rules().find((r) => r.selector === '.session-action');
  assert.equal(declaration(shared.body, 'width'), '28px');
  assert.equal(declaration(shared.body, 'font-size'), '14px');

  const inPill = rules().find((r) => r.selector === '.file-tab-close-btn');
  assert.equal(declaration(inPill.body, 'width'), '16px');
});

test('every icon button uses one of the two box sizes', () => {
  const offenders = [];
  for (const { selector, body } of rules()) {
    if (selector.includes('svg')) { continue; }  // glyph rules — next test
    if (!/(btn|action|trigger|send|toggle|clear|jump|expander)\b/.test(selector)) { continue; }
    if (EXEMPT.some((re) => re.test(selector))) { continue; }
    const width = declaration(body, 'width');
    if (width && !BOX.has(width) && width.endsWith('px')) {
      offenders.push(`${selector} -> width: ${width}`);
    }
  }
  assert.deepEqual(offenders, []);
});

test('every icon glyph uses one of the two glyph sizes', () => {
  const offenders = [];
  for (const { selector, body } of rules()) {
    const isGlyphRule = selector.includes('svg');
    const isIconButton = /(btn|action|trigger|send|toggle|clear|jump)\b/.test(selector);
    if (!isGlyphRule && !isIconButton) { continue; }
    if (EXEMPT.some((re) => re.test(selector))) { continue; }
    const size = isGlyphRule
      ? declaration(body, 'width')
      : declaration(body, 'font-size');
    if (size && !GLYPH.has(size) && size.endsWith('px')) {
      offenders.push(`${selector} -> ${size}`);
    }
  }
  assert.deepEqual(offenders, []);
});

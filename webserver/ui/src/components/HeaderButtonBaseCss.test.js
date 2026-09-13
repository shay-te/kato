// No rule may style a header's buttons BY DESCENT.
//
// The app header once carried ``header button:not(.header-status)``, which
// reached every ``<button>`` inside every ``<header>`` in the app — ten of
// them — and forced a 28px icon disc on each. Two distinct failures came out
// of that one selector:
//
//   1. Components that DID state a size lost the cascade at (0,1,2) vs (0,1,0)
//      — .files-tab-repo-commits-btn wanted 16px and rendered 28px — so three
//      of them grew a scoped override whose only job was to out-specify it.
//   2. Components that state NO size inherited an icon box they never asked
//      for. ``.permission-roster-chip`` is a TEXT pill carrying a task id, and
//      it is PORTALED into the header, so nothing in Header.jsx shows it is
//      there. It rendered as a fat oval with the status text colliding into
//      it: the "ugly circle", reported many times.
//
// Wrapping the selector in ``:where()`` fixed (1) and could never fix (2) —
// there is nothing to out-rank a zero-specificity base with. A distance rule
// cannot be made safe by lowering its specificity; it has to stop reaching.
// So the style is now an opt-in class, ``.header-icon-btn``, and this test
// guards the shape of the selector rather than the outcome of one cascade.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const ROOT = new URL('../../..', import.meta.url).pathname;
const CSS = readFileSync(join(ROOT, 'static/css/app.css'), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '');
const HEADER_JSX = readFileSync(join(ROOT, 'ui/src/components/Header.jsx'), 'utf8');

// Every selector in the compiled sheet, one per comma-separated part.
function selectors() {
  const out = [];
  const rule = /([^{}]+)\{([^{}]*)\}/g;
  let m = rule.exec(CSS);
  while (m !== null) {
    for (const sel of m[1].split(',')) { out.push({ sel: sel.trim(), decls: m[2] }); }
    m = rule.exec(CSS);
  }
  return out;
}

// Properties that decide a button's BOX. These are the ones a distance rule
// must never impose: a component that renders text cannot survive inheriting
// them, and it has no way to know they were applied.
const BOX = ['width', 'height', 'border-radius', 'padding'];

test('no rule reaches a header\'s buttons by descent', () => {
  // ``header <anything> button`` — a descendant combinator between a header
  // and a button. ``header button.header-status`` is NOT this: it names the
  // one element it styles, so it cannot surprise a component it has never
  // heard of.
  const offenders = selectors().filter(({ sel }) => {
    if (!/(^|[\s>+~])header([\s.#:[]|$)/.test(sel)) { return false; }
    const tail = sel.split(/[\s>+~]+/).slice(1).join(' ');
    if (!/\bbutton\b/.test(tail)) { return false; }
    // A selector that names a specific class on the button is targeted, not a
    // sweep: ``header button.header-status.is-clickable`` styles exactly one
    // thing and a new button in the header cannot accidentally match it.
    return !/button[.#]/.test(tail);
  });
  assert.deepEqual(
    offenders.map((o) => o.sel), [],
    'a header rule is styling buttons by descent again. It will reach every '
    + '<header> in the app, including elements portaled in — style an opt-in '
    + 'class instead',
  );
});

test('the header icon style is an opt-in class the header actually uses', () => {
  assert.ok(CSS.includes('\n.header-icon-btn {'), '.header-icon-btn is gone');
  const uses = HEADER_JSX.match(/className="header-icon-btn"/g) || [];
  assert.equal(
    uses.length, 2,
    'the settings + refresh buttons are the only reason this class exists; '
    + 'if the header changed, keep them opted in or delete the class',
  );
});

// Buttons that live inside a <header> and state their own box. Each must get
// it from its OWN single-class rule — no ancestor needed. A scoped override
// reappearing here means a distance rule came back with it.
const OWN_BOX = {
  'files-tab-repo-commits-btn': '16px',
  'files-tab-filter-clear': '16px',
  'orchestrator-feed-close': '26px',
  'plan-pane-close': '26px',
  'session-action': '28px',
  'files-tab-icon-btn': '28px',
  'adopt-session-close': '28px',
  'header-icon-btn': '28px',
};

for (const [cls, width] of Object.entries(OWN_BOX)) {
  test(`.${cls} states its own width, unscoped`, () => {
    const setters = selectors().filter(({ sel, decls }) => (
      sel.includes(`.${cls}`)
      && !/:hover|:disabled|:focus/.test(sel)
      && /(?:^|;)\s*width:/.test(decls)
    ));
    assert.ok(setters.length, `nothing sets a width for .${cls}`);
    for (const { sel } of setters) {
      const compounds = sel.split(/[\s>+~]+/).filter(Boolean);
      assert.equal(
        compounds.length, 1,
        `.${cls} needs an ancestor (${sel}) to state its own width — that is `
        + 'the out-specifying workaround, which means something is reaching it',
      );
    }
    const declared = setters.map(({ decls }) => /width:\s*([^;]+)/.exec(decls)[1].trim());
    assert.ok(
      declared.some((v) => v.includes(width)),
      `.${cls} should be ${width}, got ${declared.join(' / ')}`,
    );
  });
}

// The chips are TEXT — a task id, an "RO" badge. They must size to their
// label, which means declaring no box at all and having nothing impose one.
for (const cls of ['permission-roster-chip', 'files-tab-repo-readonly']) {
  test(`.${cls} is a text chip and carries no fixed box`, () => {
    for (const { sel, decls } of selectors()) {
      if (!sel.includes(`.${cls}`)) { continue; }
      for (const prop of ['width', 'height']) {
        const m = new RegExp(`(?:^|;)\\s*${prop}:\\s*([^;]+)`).exec(decls);
        assert.ok(
          !m || m[1].trim() === 'auto',
          `.${cls} is a text chip but ${sel} pins its ${prop} to ${m && m[1]}`,
        );
      }
    }
  });
}

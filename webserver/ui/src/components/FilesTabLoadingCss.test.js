// The Files pane's compiled-CSS invariants: the loading affordances, and the
// shape of a collapsed repo card.
//
// The per-repo row uses the SHARED button spinner, which is drawn for a
// button: 0.2em of border on a ~17px box. At row scale, twenty-five of those
// down the pane read as chunky blobs — "ugly curcle. fix this." So the pane
// overrides it locally; the button spinner itself must stay untouched.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const CSS = readFileSync(
  join(new URL('../../..', import.meta.url).pathname, 'static/css/app.css'),
  'utf8',
);

function ruleFor(selector) {
  const at = CSS.indexOf(`\n${selector} {`);
  assert.notEqual(at, -1, `missing rule: ${selector}`);
  return CSS.slice(at, CSS.indexOf('}', at)).replace(/\/\*[\s\S]*?\*\//g, '');
}

test('the repo-row spinner is a hairline, not the chunky button ring', () => {
  const rule = ruleFor('.files-tab-repo-loading .kato-btn-spinner');
  assert.match(rule, /border-width:\s*1\.5px/);
  assert.match(rule, /width:\s*12px/);
  // Still a ring: one transparent quadrant is what makes the spin readable.
  assert.match(rule, /border-top-color:\s*transparent/);
});

test('the shared BUTTON spinner is left alone', () => {
  // Scoped override only — changing this would restyle every in-flight
  // action button in the app.
  const rule = ruleFor('.kato-btn-spinner');
  assert.match(rule, /border:\s*0\.2em solid currentColor/);
});

test('a task with no remembered repos gets a real progress bar', () => {
  // The one case the per-repo skeleton cannot cover, because nothing knows
  // what to draw. A static line reads as a dead pane.
  const track = ruleFor('.files-tab-progress');
  assert.match(track, /overflow:\s*hidden/);
  const bar = ruleFor('.files-tab-progress-bar');
  assert.match(bar, /animation:\s*files-tab-progress-slide/);
  assert.ok(
    CSS.includes('@keyframes files-tab-progress-slide'),
    'the slide keyframes never reached the bundle',
  );
});

// ---- Specificity, not presence ------------------------------------------
//
// ``header button:not(.header-status)`` (0,1,2) near the top of app.scss was
// written for the APP header, but it matches every ``<button>`` inside every
// ``<header>`` — and ``.files-tab-repo-header`` is one. The row's own
// ``.files-tab-repo-commits-btn`` is (0,1,0), so its size LOST and these
// rendered as 28px circles in a compact row.
//
// The operator reported that circle five times. It survived because each fix
// edited the rule that was already losing, and each test asserted that a rule
// EXISTED rather than that it won. This computes the winner.

function specificity(selector) {
  // ``:not()`` contributes nothing itself — only its argument counts.
  const ids = (selector.match(/#[\w-]+/g) || []).length;
  const classes = (selector.match(/\.[\w-]+/g) || []).length
    + (selector.match(/\[[^\]]*\]/g) || []).length;
  const bare = selector.replace(/\.[\w-]+|#[\w-]+|\[[^\]]*\]|:not\(|\)/g, ' ');
  const elements = ((' ' + bare).match(/(?:^|[\s>+~])([a-z][\w-]*)/g) || []).length;
  return [ids, classes, elements];
}

// The declaration that actually applies to an element carrying `classes`
// inside the given ancestor chain, for one property.
function winningValue(property, matchesSelector) {
  const bare = CSS.replace(/\/\*[\s\S]*?\*\//g, '');
  let best = null;
  const rule = /([^{}]+)\{([^{}]*)\}/g;
  let m = rule.exec(bare);
  while (m !== null) {
    const body = m[2];
    const decl = new RegExp(`(?:^|;)\\s*${property}:\\s*([^;]+)`).exec(body);
    if (decl) {
      for (const sel of m[1].split(',')) {
        const s = sel.trim();
        if (!matchesSelector(s)) { continue; }
        const rank = [...specificity(s), m.index];
        if (best === null || rank > best.rank) {
          best = { rank, value: decl[1].trim(), selector: s };
        }
      }
    }
    m = rule.exec(bare);
  }
  return best;
}

test('the repo-row history button is NOT the 28px header circle', () => {
  // <header class="files-tab-repo-header"> … <button class="files-tab-repo-commits-btn">
  // Nothing may reach it from the header any more, so its own rule is the only
  // one that can win. The old ``header button:not(.header-status)`` is listed
  // so this still fails loudly if that rule is ever reinstated.
  const applies = (sel) => sel === 'header button:not(.header-status)'
    || sel.includes('files-tab-repo-commits-btn');
  const won = winningValue('width', applies);
  assert.ok(won, 'nothing sets a width on that button');
  assert.notEqual(
    won.selector, 'header button:not(.header-status)',
    'the global header rule is winning again — the row button is a 28px circle',
  );
  assert.match(won.value, /^16px$/);
});

// ---- The repo card closes with a curve, in every state -------------------
//
// Two operator reports, the same complaint twice: "when tree collapsed make
// it rounded also at the bottom", then — of an EXPANDED card — "also keep it
// round corner at the bottom". The card is round all round now. Only the
// header still depends on state, because expanded it hands off to the tree.

test('the repo card is rounded at the BOTTOM in every state', () => {
  // The section carries one radius, so no state can lose the curve. Asserted
  // as "nothing overrides it": a state rule reintroducing a top-only pair is
  // how the expanded card ended up cut off square the first time.
  const won = winningValue(
    'border-radius',
    (sel) => sel.startsWith('.files-tab-repo')
      && !sel.includes('.files-tab-repo-')
      && !sel.includes(' '),
  );
  assert.equal(won.selector, '.files-tab-repo');
  // One value = all four corners. A top-only pair is four values with spaces.
  assert.match(
    won.value, /^\S+$/,
    `the card still has a square bottom (${won.value})`,
  );
});

test('collapsed, the HEADER closes with the card', () => {
  // Specificity, not presence: the base header rule sets a top-only pair, so
  // the collapsed rule only helps if it actually WINS. The header paints the
  // fill and sits flush to the section edge, so a square corner here shows
  // straight through the section's curve.
  const won = winningValue(
    'border-radius',
    (sel) => sel === '.files-tab-repo-header'
      || sel === '.files-tab-repo.is-collapsed .files-tab-repo-header',
  );
  assert.equal(
    won.selector, '.files-tab-repo.is-collapsed .files-tab-repo-header',
  );
  assert.match(won.value, /^\S+$/);
});

test('expanded, the header keeps its square bottom', () => {
  // A curve here would cut a notch out of a card that continues into a tree.
  assert.match(ruleFor('.files-tab-repo-header'), /border-radius:[^;]*0\s+0/);
});

// ---- A header's tooltip must clear the cards below it --------------------
//
// Operator: "tooltip goes under." Every repo header is a sticky, z-indexed
// box — which makes it a STACKING CONTEXT, so the tooltip's own z-index:1000
// ranks it only inside its header, never against the page. All the headers
// share one z, so later ones in the DOM win, and a downward tooltip lands
// under the next card's header.

test('the hovered repo header outranks its siblings', () => {
  const rule = ruleFor('.files-tab-repo-header:focus-within');
  const lifted = /--sticky-header-z:\s*(\d+)/.exec(rule);
  assert.ok(lifted, 'the hovered header no longer lifts — tooltips go under');

  // Against the resting value the other headers are using.
  const resting = /z-index:\s*var\(--sticky-header-z,\s*(\d+)\)/
    .exec(ruleFor('.sticky-section-header'));
  assert.ok(resting, '.sticky-section-header no longer reads the z variable');
  assert.ok(
    Number(lifted[1]) > Number(resting[1]),
    `lifted ${lifted[1]} must beat a sibling's ${resting[1]}`,
  );
});

test('the lift stays under the layers meant to cover the pane', () => {
  // A repo row lifting itself over an error banner or a modal would be a
  // worse bug than the one this fixes.
  const lifted = Number(
    /--sticky-header-z:\s*(\d+)/
      .exec(ruleFor('.files-tab-repo-header:focus-within'))[1],
  );
  assert.ok(lifted < 50, `the lift (${lifted}) reaches the banner layer`);
});

test('the pointer lifts it too, not just keyboard focus', () => {
  // The rules above read the :focus-within selector, because a keyboard
  // operator never triggers :hover and the fix would otherwise be
  // mouse-only. :hover has to share that rule, or it is now keyboard-only
  // instead — which is the case the operator actually reported.
  assert.match(
    CSS,
    /\.files-tab-repo-header:hover,\s*\n\.files-tab-repo-header:focus-within \{/,
    ':hover no longer shares the lift rule',
  );
});

test('the loading skeleton has the SAME radius as the loaded card', () => {
  // It mirrors the loaded card so the pane fills in without re-laying out. A
  // curve that appeared or vanished when the tree landed is that same shift —
  // which is why ``.is-loading`` must not override the radius at all.
  const won = winningValue(
    'border-radius',
    (sel) => sel === '.files-tab-repo' || sel === '.files-tab-repo.is-loading',
  );
  assert.equal(won.selector, '.files-tab-repo');
});

test('the read-only badge is not squeezed into that circle either', () => {
  // "RO" is two characters; a square icon box clips it. The badge now sizes to
  // its label by declaring NO width — which only works because nothing imposes
  // one. It used to need ``width: auto`` scoped under .files-tab-repo-header
  // just to undo the box the header rule forced on it.
  const applies = (sel) => sel === 'header button:not(.header-status)'
    || sel.includes('files-tab-repo-readonly');
  const won = winningValue('width', applies);
  assert.equal(
    won, null,
    `something is pinning the RO badge's width (${won && won.selector}); `
    + 'a two-character text badge must size to its label',
  );
});


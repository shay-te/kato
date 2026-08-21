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

function ruleBodyContaining(selector, text) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const matches = [...css.matchAll(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, 'g'))];
  const match = matches.find((entry) => {
    return entry[1].includes(text);
  });
  assert.ok(match, `expected ${selector} rule containing ${text} to exist`);
  return match[1];
}

function assertDeclaration(body, property, value) {
  const declaration = new RegExp(`${property}\\s*:\\s*${value}\\s*;`);
  assert.match(body, declaration);
}

test('DiffPane file cards clip diff rows inside rounded corners', () => {
  const body = ruleBody('.diff-pane .diff-file');
  assertDeclaration(body, 'overflow', 'clip');
});

test('Inline comment widget fits the visible diff width, not the table width', () => {
  // The widget lives in a <td colspan> of a ``width: max-content`` table,
  // so it used to be as wide as the file's longest source line — which
  // put its Cancel / Add comment buttons off the right edge of the pane.
  // Measured in Chrome on a 598px pane: 1304px wide, submit button's
  // right edge at 1296px. Sizing it off the scroller's own inline size
  // brings it back to 598px with the buttons at 590px.
  const bodyRule = ruleBody('.diff-pane .diff-file-body');
  assertDeclaration(bodyRule, 'container-type', 'inline-size');
  const host = ruleBody('.diff-line-comments-host');
  assertDeclaration(host, 'width', '100cqi');
  assertDeclaration(host, 'box-sizing', 'border-box');
});

test('Shared sticky section header utility owns sticky mechanics', () => {
  const body = ruleBody('.sticky-section-header');
  assertDeclaration(body, 'position', 'sticky');
  assertDeclaration(body, 'top', 'var\\(--sticky-header-top, 0\\)');
  assertDeclaration(body, 'z-index', 'var\\(--sticky-header-z, 4\\)');
});

test('DiffPane file headers keep their visual styling on the shared sticky header', () => {
  const body = ruleBody('.diff-pane .diff-file-header');
  assertDeclaration(body, '--sticky-header-z', '3');
  assertDeclaration(body, 'background', '#2a2a2a');
});

test('DiffPane files run edge to edge inside the panel card', () => {
  // The PANEL is the rounded card now, so the file boxes are full-bleed
  // and square — an inset box-in-a-box gave up panel width for nothing.
  const paneBody = ruleBody('.diff-pane-body');
  const fileBody = ruleBody('.diff-pane .diff-file');
  const headerBody = ruleBody('.diff-pane .diff-file-header');

  assertDeclaration(paneBody, 'padding', '0');
  assertDeclaration(fileBody, 'border', '0');
  assertDeclaration(fileBody, 'border-radius', '0');
  assertDeclaration(fileBody, 'margin', '0');
  assertDeclaration(headerBody, 'padding', '6px 10px');
});

test('Badge, chip, and pill classes share the global pill radius', () => {
  const rootBody = ruleBody(':root');
  // Sass normalizes attribute-selector quoting ([class*="badge"] ->
  // [class*=badge]); both are equivalent CSS, so match the unquoted form.
  const safetyBody = ruleBody(':where([class*=badge], [class*=chip], [class*=pill])');
  assertDeclaration(rootBody, '--radius-pill', '999px');
  assertDeclaration(safetyBody, 'border-radius', 'var\\(--radius-pill\\)');

  const cssWithoutComments = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const rulePattern = /([^{}]+)\{([^{}]*)\}/g;
  const failures = [];
  for (const match of cssWithoutComments.matchAll(rulePattern)) {
    const selector = match[1].trim();
    const body = match[2];
    const isPillish = /(?:badge|chip|pill)/.test(selector);
    const radius = body.match(/border-radius\s*:\s*([^;]+)\s*;/);
    if (!isPillish || !radius) { continue; }
    if (!/^(var\(--radius-pill\)|999px)$/.test(radius[1].trim())) {
      failures.push(`${selector} -> ${radius[1].trim()}`);
    }
  }
  assert.deepEqual(failures, []);
});

test('DiffPane file headers draw an opaque face above scrolling diff rows', () => {
  // Hairlines top and bottom, no radius: with the file boxes full-bleed
  // the header IS the seam between one file and the next.
  const body = ruleBody('.diff-pane .diff-file-header::before');
  assertDeclaration(body, 'background', '#0a0a0a');
  assertDeclaration(body, 'border-top', '1px solid #2a2a2a');
  assertDeclaration(body, 'border-bottom', '1px solid #2a2a2a');
});

test('DiffPane comments block squares off with its full-bleed file', () => {
  const body = ruleBody('.diff-pane .diff-file-comments');
  assertDeclaration(body, 'border-radius', '0');
});

test('DiffPane uses Bitbucket-style hunk colors (saturated red/green)', () => {
  const body = ruleBodyContaining('.diff-file', '--diff-code-insert-background-color');
  assertDeclaration(body, '--diff-text-color', '#b6c2cf');
  assertDeclaration(body, '--diff-code-insert-background-color', '#1d2b27');
  assertDeclaration(body, '--diff-gutter-insert-background-color', '#164b35');
  assertDeclaration(body, '--diff-code-insert-edit-background-color', '#216e4e');
  assertDeclaration(body, '--diff-code-delete-background-color', '#3a2423');
  assertDeclaration(body, '--diff-gutter-delete-background-color', '#5d1f1a');
  assertDeclaration(body, '--diff-code-delete-edit-background-color', '#ae2e24');
});

test('DiffPane gutter line numbers match Bitbucket dark metrics', () => {
  const body = ruleBody('.diff-file .diff-gutter');
  const insertBody = ruleBody('.diff-file .diff-gutter-insert,\n.diff-file .diff-gutter-delete');
  const insertOnlyBody = ruleBodyContaining('.diff-file .diff-gutter-insert', 'background');
  const deleteOnlyBody = ruleBodyContaining('.diff-file .diff-gutter-delete', 'background');

  assertDeclaration(body, 'background', '#303134');
  assertDeclaration(body, 'color', '#b6c2cf');
  assertDeclaration(body, 'font-variant-numeric', 'tabular-nums');
  assertDeclaration(body, 'padding', '0 6px');
  assertDeclaration(body, 'text-align', 'right');
  assertDeclaration(insertBody, 'color', '#b6c2cf');
  assertDeclaration(insertOnlyBody, 'background', '#164b35');
  assertDeclaration(deleteOnlyBody, 'background', '#5d1f1a');
});

test('Diff file comments panel rounds the bottom of the file card', () => {
  const body = ruleBody('.diff-file-comments');
  assertDeclaration(body, 'border-radius', '0 0 10px 10px');
});

test('DiffPane skips offscreen diff bodies during scroll', () => {
  const body = ruleBody('.diff-pane .diff-file-body');
  assertDeclaration(body, 'content-visibility', 'auto');
  assertDeclaration(body, 'contain-intrinsic-size', 'auto 720px');
});

test('Files tab body scrolls changed-file trees vertically', () => {
  const body = ruleBody('.files-tab-body');
  assertDeclaration(body, 'overflow-y', 'auto');
  assertDeclaration(body, 'overflow-x', 'hidden');
});

test('Files tab repo headers stick while scrolling a repository', () => {
  const repoBody = ruleBody('.files-tab-repo');

  assertDeclaration(repoBody, 'overflow', 'visible');
});

test('Changed-file tree guide line stays out of the chevron lane', () => {
  const body = ruleBody('.diff-file-tree-guide');
  assertDeclaration(body, 'left', '22px');
  assert.match(body, /width\s*:\s*calc\(var\(--depth\) \* 22px\)\s*;/);
  assert.match(body, /background-image\s*:\s*repeating-linear-gradient\(/);
});

test('Changed-file tree gives folders lighter weight than files', () => {
  const folderBody = ruleBody('.files-changed-tree-folder');
  const fileBody = ruleBody('.files-changed-tree-label');
  assertDeclaration(folderBody, 'font-weight', '600');
  assertDeclaration(fileBody, 'font-weight', '750');
});

test('Changed-file tree hover and selected states use opaque backgrounds', () => {
  const hoverBody = ruleBody('.diff-file-tree-row.is-file:hover');
  const selectedBody = ruleBody('.diff-file-tree-row.selected');
  const selectedHoverBody = ruleBody('.diff-file-tree-row.is-file.selected:hover');

  assertDeclaration(hoverBody, 'background', '#2a2a2a');
  assertDeclaration(selectedBody, 'background', '#1f2937');
  assertDeclaration(selectedHoverBody, 'background', '#1f2937');
});

test('Project tree row hover stays transparent without opacity changes', () => {
  const body = ruleBody('.tree-row:hover');

  assertDeclaration(body, 'background', 'transparent');
  assert.doesNotMatch(body, /opacity\s*:/);
});

test('Diff syntax colors match the Bitbucket palette', () => {
  // The Bitbucket-aligned palette consolidates tokens into three groups:
  //   - pink salmon (#fca5a5) for keywords / booleans / None
  //   - cyan-blue (#93c5fd) for the identifier cluster: function names,
  //     class names, types (builtin), attr-name, property, numbers
  //   - amber (#f59e0b) for strings
  // Selectors are bundled with commas, so the single-selector matcher
  // doesn't fit. Walk every rule block and find one whose selector list
  // contains ``.diff-file .token.<class>`` AND whose body sets the
  // expected colour.
  function ruleListBodyFor(tokenClass) {
    const re = /([^{}]+)\{([^}]*)\}/g;
    const want = `.diff-file .token.${tokenClass}`;
    let match;
    while ((match = re.exec(css)) !== null) {
      const selectors = match[1];
      const body = match[2];
      if (selectors.split(',').some((s) => s.trim() === want)) {
        return body;
      }
    }
    return null;
  }
  function assertTokenColor(tokenClass, hex) {
    const body = ruleListBodyFor(tokenClass);
    assert.ok(body, `expected rule for .diff-file .token.${tokenClass}`);
    assertDeclaration(body, 'color', hex);
  }
  assertTokenColor('keyword', '#fca5a5');
  assertTokenColor('boolean', '#fca5a5');
  assertTokenColor('function', '#93c5fd');
  assertTokenColor('class-name', '#93c5fd');
  assertTokenColor('builtin', '#93c5fd');
  assertTokenColor('number', '#93c5fd');
  assertTokenColor('property', '#93c5fd');
  assertTokenColor('attr-name', '#93c5fd');
  assertTokenColor('string', '#ffd43b');
});

test('Bitbucket comment card: avatar, collapse chevron, dot actions', () => {
  const avatar = ruleBody('.diff-file-comment-avatar');
  assertDeclaration(avatar, 'border-radius', '50%');

  const sourceBadge = ruleBody('.diff-file-comment-source');
  assertDeclaration(sourceBadge, 'border-radius', 'var\\(--radius-pill\\)');

  const statusPill = ruleBody('.diff-file-comment-pill');
  assertDeclaration(statusPill, 'border-radius', 'var\\(--radius-pill\\)');

  const collapse = ruleBody('.diff-file-comment-collapse');
  assertDeclaration(collapse, 'cursor', 'pointer');

  // The action icons + collapse chevron live in a sticky, right-pinned tail
  // so they stay visible when the diff scrolls horizontally. The tail now
  // carries the ``margin-left: auto`` that used to sit on the chevron.
  const tail = ruleBody('.diff-file-comment-head-tail');
  assertDeclaration(tail, 'margin-left', 'auto');
  assertDeclaration(tail, 'position', 'sticky');

  // Collapsed bubble state rule must exist.
  ruleBody('.diff-file-comment.is-collapsed');
});

test('Comment editor has a formatting toolbar', () => {
  const btn = ruleBody('.diff-file-comments-toolbar-btn');
  assertDeclaration(btn, 'cursor', 'pointer');
  ruleBody('.diff-file-comments-toolbar');
});

test('the removed diff-header controls leave no CSS behind', () => {
  // The chevron, the header path button and the "View file" button all
  // came out when the tab took over naming the file and switching views.
  // Their rules went with them — dead selectors are how a "why is this
  // here?" block survives three refactors.
  assert.doesNotMatch(css, /\.diff-file-collapse-toggle/);
  assert.doesNotMatch(css, /\.diff-file-open-as-file/);
  assert.doesNotMatch(css, /\.diff-file-path/);
});

test('Diff context expander has Bitbucket-style controls', () => {
  const rowBody = ruleBody('.diff-context-expander-inner');
  const buttonBody = ruleBody('.diff-context-expander-btn');

  assertDeclaration(rowBody, 'background', '#2a2a2a');
  assertDeclaration(rowBody, 'font-family', 'ui-monospace, monospace');
  assertDeclaration(buttonBody, 'width', '16px');
  assertDeclaration(buttonBody, 'border-radius', '4px');
});

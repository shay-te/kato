// Every ``tooltip-*`` class a component applies must exist in the sheet.
//
// This is a real bug that shipped: the chats button carried
// ``tooltip-below``, a class nobody ever wrote a rule for. It looked like a
// positioning fix in the JSX and did NOTHING, so the tooltip kept using the
// default centred anchor and kept being clipped by the chat panel's left
// edge. A typo'd or invented modifier class is invisible at runtime — no
// error, no warning, just a fix that silently isn't applied.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

const ROOT = new URL('..', import.meta.url).pathname;
const css = readFileSync(join(ROOT, '../../static/css/app.css'), 'utf8');

function sourceFiles(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) { sourceFiles(full, out); continue; }
    if (/\.jsx?$/.test(name) && !/\.test\.jsx?$/.test(name)) { out.push(full); }
  }
  return out;
}

test('every tooltip modifier class used in JSX has a CSS rule', () => {
  const used = new Map();
  for (const file of sourceFiles(ROOT)) {
    const text = readFileSync(file, 'utf8');
    // ``(?<![\w-])`` so a component's own prefixed class (``tab-tooltip-head``)
    // isn't mistaken for a bare ``[data-tooltip]`` positioning modifier.
    for (const match of text.matchAll(/(?<![\w-])tooltip-[a-z-]+\b/g)) {
      const where = used.get(match[0]) || new Set();
      where.add(file.slice(ROOT.length));
      used.set(match[0], where);
    }
  }
  assert.ok(used.size > 0, 'no tooltip classes found — did the scan break?');

  const missing = [];
  for (const [cls, files] of used) {
    // A rule exists if the class name appears in a selector position.
    if (!new RegExp(`\\.${cls}[\\s,:{.]`).test(css)) {
      missing.push(`${cls} (used in ${[...files].join(', ')})`);
    }
  }
  assert.deepEqual(
    missing, [],
    `tooltip classes with no CSS rule — these do nothing:\n  ${missing.join('\n  ')}`,
  );
});

test('the chats button anchors its tooltip away from the panel edge', () => {
  // It sits near the chat panel's LEFT edge; the default centred anchor
  // hangs a 320px tooltip past that edge, where it is clipped.
  const source = readFileSync(join(ROOT, 'components/ChatsMenu.jsx'), 'utf8');
  assert.match(source, /tooltip-start/);
  assert.doesNotMatch(source, /tooltip-below/);
});

// The duplicate-code gate must actually READ every source file.
//
// jscpd silently skips any file over ``maxLines`` (default 1000) or
// ``maxSize`` (default 100kb) — no warning, no error, the file simply is not
// checked. The gate ran that way for a long time. It never looked at app.scss
// (12k lines), FilesTab.jsx, EventLog.jsx, SessionDetail.jsx,
// useSessionStream.js or api.js, so its clean "0.03%" hid four real clones.
// And when DiffFileWithComments.jsx grew past 1000 lines, one of the two
// intentional clones dropped out of the count — which read as an improvement.
//
// A fixed ceiling just moves the trap: files keep growing. So this measures
// the real sources on every run and fails while there is still headroom,
// before the gate goes blind again.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const UI_ROOT = new URL('../..', import.meta.url).pathname;
const CONFIG = JSON.parse(readFileSync(join(UI_ROOT, '.jscpd.json'), 'utf8'));

// Fail at half the limit, so a file approaching it is caught early rather
// than on the day it silently falls out of the scan.
const HEADROOM = 2;

function parseSize(value) {
  const match = /^(\d+(?:\.\d+)?)\s*(b|kb|mb|gb)?$/i.exec(String(value || '').trim());
  if (!match) { return NaN; }
  const unit = (match[2] || 'b').toLowerCase();
  const factor = { b: 1, kb: 1024, mb: 1024 ** 2, gb: 1024 ** 3 }[unit];
  return Number(match[1]) * factor;
}

// Exactly what the gate scans: src, the configured formats, tests excluded.
const EXTENSIONS = new Set(['.js', '.jsx', '.scss']);
function scannedSources(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) { scannedSources(full, out); continue; }
    if (/\.test\./.test(name)) { continue; }
    const ext = name.slice(name.lastIndexOf('.'));
    if (EXTENSIONS.has(ext)) { out.push(full); }
  }
  return out;
}

const SOURCES = scannedSources(join(UI_ROOT, 'src')).map((file) => ({
  file: relative(UI_ROOT, file),
  lines: readFileSync(file, 'utf8').split('\n').length,
  bytes: statSync(file).size,
}));

test('the scan found the sources it is meant to check', () => {
  // A walk that returned nothing would make every assertion below pass.
  assert.ok(SOURCES.length > 100, `only ${SOURCES.length} sources found under src`);
});

test('maxLines is set explicitly — the 1000-line default is the silent-skip trap', () => {
  assert.ok(
    Number.isFinite(CONFIG.maxLines),
    '.jscpd.json has no maxLines, so jscpd skips every file over 1000 lines without saying so',
  );
});

test('maxSize is set explicitly — the 100kb default skips the stylesheet', () => {
  assert.ok(
    Number.isFinite(parseSize(CONFIG.maxSize)),
    '.jscpd.json has no maxSize, so jscpd skips every file over 100kb without saying so',
  );
});

test('the largest source is far inside maxLines', () => {
  const biggest = SOURCES.reduce((a, b) => (b.lines > a.lines ? b : a));
  assert.ok(
    biggest.lines * HEADROOM <= CONFIG.maxLines,
    `${biggest.file} has ${biggest.lines} lines; maxLines ${CONFIG.maxLines} leaves less than `
    + `${HEADROOM}x headroom. Raise it — past the limit this file is silently not checked`,
  );
});

test('the largest source is far inside maxSize', () => {
  const biggest = SOURCES.reduce((a, b) => (b.bytes > a.bytes ? b : a));
  const limit = parseSize(CONFIG.maxSize);
  assert.ok(
    biggest.bytes * HEADROOM <= limit,
    `${biggest.file} is ${biggest.bytes} bytes; maxSize ${CONFIG.maxSize} leaves less than `
    + `${HEADROOM}x headroom. Raise it — past the limit this file is silently not checked`,
  );
});

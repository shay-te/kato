// The instant-restore CONTRACT.
//
// The behaviour is tested elsewhere (createDataStore hydrates, the adapter
// stores, treeChild is wired). What this file guards is the thing that decays:
// that the registry in ./durableTypes.js still describes reality as the cache
// grows. The operator: "write it as features so as we expend it will never
// break. i am tired of you are reopening bugs."
//
// So these are structural assertions read off the slice DIRECTORY, not off a
// hard-coded list a future change can forget to update. Add a sixth slice and
// the first test fails until you classify it. Delete `persist:` from a durable
// slice and the third test fails. Neither can slip through review as "just a
// small store change".

import { describe, test, expect } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

import { DURABLE_TYPES, VOLATILE_TYPES, durableConfig } from './durableTypes.js';

// Resolved from the project root, not from ``import.meta.url``: vite rewrites
// module URLs, so the URL form resolved to a path inside index.js and every
// assertion below read an empty directory — which would have made the whole
// contract pass while checking nothing.
const SLICES_DIR = join(process.cwd(), 'src/stores/taskCache/slices');

// Every child store, discovered from disk: `diffChild.js` -> `diff`.
function sliceFiles() {
  const found = readdirSync(SLICES_DIR)
    .filter((f) => f.endsWith('Child.js'))
    .map((file) => ({
      type: file.replace(/Child\.js$/, ''),
      file,
      source: readFileSync(join(SLICES_DIR, file), 'utf8'),
    }));
  // A contract that reads nothing asserts nothing. If this directory ever
  // moves, fail here rather than reporting a clean sweep over zero slices.
  if (found.length < 5) {
    throw new Error(`only ${found.length} slices found in ${SLICES_DIR} — the `
      + 'contract cannot be checked against a directory it cannot read');
  }
  return found;
}

describe('instant restore — the durability registry describes every slice', () => {
  test('every cache slice is classified as durable or volatile', () => {
    const unclassified = sliceFiles()
      .map(({ type }) => type)
      .filter((type) => !(type in DURABLE_TYPES) && !(type in VOLATILE_TYPES));
    expect(unclassified, 'a new task-cache slice was added without deciding '
      + 'whether it survives a page reload. Add it to DURABLE_TYPES or to '
      + 'VOLATILE_TYPES (with a reason) in durableTypes.js').toEqual([]);
  });

  test('the registry names no type that does not exist', () => {
    const real = new Set(sliceFiles().map(({ type }) => type));
    const ghosts = [...Object.keys(DURABLE_TYPES), ...Object.keys(VOLATILE_TYPES)]
      .filter((type) => !real.has(type));
    expect(ghosts, 'the registry lists a slice that is gone — a stale entry '
      + 'here is how the next reader is misled').toEqual([]);
  });

  test('a slice is wired to persistence if and only if it is declared durable', () => {
    for (const { type, file, source } of sliceFiles()) {
      const wired = /\bpersist:/.test(source);
      const declared = type in DURABLE_TYPES;
      expect(wired, `${file}: ${declared
        ? 'is declared DURABLE but passes no `persist:` adapter — a reload '
          + 'will show a spinner for data the browser already has'
        : 'passes a `persist:` adapter but is declared VOLATILE — it will '
          + 'restore stale data the registry says must not be restored'
      }`).toBe(declared);
    }
  });

  test('a durable slice reads its config from the registry, never inline', () => {
    // Hard-coding `{ name: 'tree' }` at the call site is how the registry
    // stops being the source of truth: it keeps compiling, keeps working, and
    // silently stops describing anything.
    for (const { type, file, source } of sliceFiles()) {
      if (!(type in DURABLE_TYPES)) { continue; }
      expect(source, `${file} should call durableConfig('${type}')`)
        .toContain(`durableConfig('${type}')`);
    }
  });

  test('every volatile type carries a reason a reader could disprove', () => {
    for (const [type, reason] of Object.entries(VOLATILE_TYPES)) {
      expect(typeof reason, `${type} needs a prose reason`).toBe('string');
      expect(reason.length, `${type}'s reason is too thin to be checkable`)
        .toBeGreaterThan(40);
    }
  });

  test('durableConfig answers for durable types and refuses volatile ones', () => {
    expect(durableConfig('tree')).toMatchObject({ name: 'tree' });
    expect(durableConfig('diff')).toBe(null);
    expect(durableConfig('nope')).toBe(null);
  });
});

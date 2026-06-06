import assert from 'node:assert/strict';
import test, { beforeEach } from 'node:test';

import { toolDecisionsStore } from './toolDecisionsStore.js';

// No DOM here → storage.js degrades to a no-op (verified by
// useToolMemory.test.js); these tests pin the in-memory + pub/sub logic
// that both the permission prompt and the settings panel rely on.
beforeEach(() => { toolDecisionsStore.forget(); });

test('setDecision + recall round-trip; entries() is name-sorted', () => {
  toolDecisionsStore.setDecision('Write', 'allow');
  toolDecisionsStore.setDecision('Bash', 'deny');
  assert.equal(toolDecisionsStore.recall('Write'), 'allow');
  assert.equal(toolDecisionsStore.recall('Bash'), 'deny');
  assert.deepEqual(toolDecisionsStore.entries(), [
    { tool: 'Bash', decision: 'deny' },
    { tool: 'Write', decision: 'allow' },
  ]);
});

test('remember(allow=false) maps to deny', () => {
  toolDecisionsStore.remember('Edit', false);
  assert.equal(toolDecisionsStore.recall('Edit'), 'deny');
});

test('recall returns null for an unknown / empty tool', () => {
  assert.equal(toolDecisionsStore.recall('Nope'), null);
  assert.equal(toolDecisionsStore.recall(''), null);
});

test('forget(tool) drops one; forget() clears all', () => {
  toolDecisionsStore.setDecision('Bash', 'allow');
  toolDecisionsStore.setDecision('Edit', 'allow');
  toolDecisionsStore.forget('Bash');
  assert.equal(toolDecisionsStore.recall('Bash'), null);
  assert.equal(toolDecisionsStore.recall('Edit'), 'allow');
  toolDecisionsStore.forget();
  assert.deepEqual(toolDecisionsStore.entries(), []);
});

test('subscribe fires the snapshot once on subscribe, then on each change', () => {
  const seen = [];
  const unsub = toolDecisionsStore.subscribe((s) => seen.push(s));
  toolDecisionsStore.setDecision('Bash', 'allow');
  unsub();
  toolDecisionsStore.setDecision('Edit', 'allow'); // after unsub → not seen
  // initial fire + one change.
  assert.equal(seen.length, 2);
  assert.equal(seen[1].Bash, 'allow');
});

test('setDecision to the SAME value does not emit (no render loop)', () => {
  toolDecisionsStore.setDecision('Bash', 'allow');
  const seen = [];
  const unsub = toolDecisionsStore.subscribe((s) => seen.push(s));
  toolDecisionsStore.setDecision('Bash', 'allow'); // unchanged → no emit
  unsub();
  assert.equal(seen.length, 1); // just the initial subscribe fire
});

test('forget of an absent tool does not emit', () => {
  const seen = [];
  const unsub = toolDecisionsStore.subscribe((s) => seen.push(s));
  toolDecisionsStore.forget('Ghost');
  unsub();
  assert.equal(seen.length, 1);
});

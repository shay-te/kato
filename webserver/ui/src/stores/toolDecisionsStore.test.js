import assert from 'node:assert/strict';
import test, { beforeEach } from 'node:test';

import { toolDecisionsStore } from './toolDecisionsStore.js';

// No DOM here → storage.js degrades to a no-op (verified by
// useToolMemory.test.js); these tests pin the in-memory + pub/sub logic
// that both the permission prompt and the settings panel rely on.
beforeEach(() => { toolDecisionsStore.forget(); });

test('setDecision + recall round-trip; entries() is sorted with key/tool/command', () => {
  toolDecisionsStore.setDecision('Write', 'allow');
  toolDecisionsStore.setDecision('Bash', 'deny');
  assert.equal(toolDecisionsStore.recall('Write'), 'allow');
  assert.equal(toolDecisionsStore.recall('Bash'), 'deny');
  assert.deepEqual(toolDecisionsStore.entries(), [
    { key: 'Bash', tool: 'Bash', command: '', decision: 'deny' },
    { key: 'Write', tool: 'Write', command: '', decision: 'allow' },
  ]);
});

test('command-keyed: an exact Bash command is its own entry, recalled by command', () => {
  toolDecisionsStore.remember('Bash', true, 'mvn -B verify');
  // Recalled only by the exact command — not the bare tool, not another cmd.
  assert.equal(toolDecisionsStore.recall('Bash', 'mvn -B verify'), 'allow');
  assert.equal(toolDecisionsStore.recall('Bash'), null);
  assert.equal(toolDecisionsStore.recall('Bash', 'docker run x'), null);
  // One entry, parsed into tool + command (key is opaque — don't assert it).
  const rows = toolDecisionsStore.entries();
  assert.equal(rows.length, 1);
  assert.equal(rows[0].tool, 'Bash');
  assert.equal(rows[0].command, 'mvn -B verify');
  assert.equal(rows[0].decision, 'allow');
});

test('command-keyed: two commands are independent; key-based clear/scope', () => {
  toolDecisionsStore.remember('Bash', true, 'mvn verify');
  toolDecisionsStore.remember('Bash', true, 'docker run x');
  // Keys come from entries() — the panel never rebuilds them by hand.
  const keyOf = (cmd) => toolDecisionsStore.entries().find((r) => r.command === cmd).key;
  // Re-scope one by its key without touching the other.
  toolDecisionsStore.setDecisionByKey(keyOf('docker run x'), 'deny');
  assert.equal(toolDecisionsStore.recall('Bash', 'docker run x'), 'deny');
  assert.equal(toolDecisionsStore.recall('Bash', 'mvn verify'), 'allow');
  // Clear one by key.
  toolDecisionsStore.forgetByKey(keyOf('docker run x'));
  assert.equal(toolDecisionsStore.recall('Bash', 'docker run x'), null);
  assert.equal(toolDecisionsStore.recall('Bash', 'mvn verify'), 'allow');
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

import assert from 'node:assert/strict';
import test, { beforeEach } from 'node:test';

import { toast, toastStore } from './toastStore.js';

// Module-scoped state means tests must clear between cases.
beforeEach(() => { toastStore.clear(); });


test('push returns an id and adds the toast to subscribers', () => {
  const seen = [];
  const unsub = toastStore.subscribe((toasts) => seen.push(toasts.length));
  const id = toastStore.push({ kind: 'info', message: 'hi', durationMs: 0 });
  assert.equal(typeof id, 'number');
  // Subscribe fires once with the initial state (0), then push fires once.
  assert.deepEqual(seen, [0, 1]);
  unsub();
});

test('dismiss removes the toast by id and emits', () => {
  const seen = [];
  const unsub = toastStore.subscribe((t) => seen.push(t.length));
  const id = toastStore.push({ message: 'go', durationMs: 0 });
  toastStore.dismiss(id);
  assert.deepEqual(seen, [0, 1, 0]);
  unsub();
});

test('dismiss with an unknown id is a no-op (no emit)', () => {
  const seen = [];
  toastStore.subscribe((t) => seen.push(t.length));
  toastStore.dismiss(99999);
  // Only the initial-subscribe fire.
  assert.deepEqual(seen, [0]);
});

test('clear empties the toast list and emits when non-empty', () => {
  toastStore.push({ message: 'a', durationMs: 0 });
  toastStore.push({ message: 'b', durationMs: 0 });
  const seen = [];
  toastStore.subscribe((t) => seen.push(t.length));
  // After subscribe: snapshot has 2.
  toastStore.clear();
  assert.deepEqual(seen, [2, 0]);
});

test('clear on empty list does NOT emit', () => {
  const seen = [];
  toastStore.subscribe((t) => seen.push(t.length));
  toastStore.clear();
  // Just the initial snapshot fire.
  assert.deepEqual(seen, [0]);
});

test('toast.info / success / warning / error route through to push with the right kind', () => {
  toast.info('hi', { durationMs: 0 });
  toast.success('done', { durationMs: 0 });
  toast.warning('be careful', { durationMs: 0 });
  toast.error('boom', { durationMs: 0 });

  let snapshot = [];
  toastStore.subscribe((t) => { snapshot = t; });
  const kinds = snapshot.map((t) => t.kind);
  assert.deepEqual(kinds, ['info', 'success', 'warning', 'error']);
});

test('toast.show forwards full opts (title + message + kind)', () => {
  toast.show({
    kind: 'warning', title: 'Heads up', message: 'check this', durationMs: 0,
  });
  let snap = [];
  toastStore.subscribe((t) => { snap = t; });
  assert.equal(snap[0].title, 'Heads up');
  assert.equal(snap[0].kind, 'warning');
});

test('default kind is "info" when not specified', () => {
  toastStore.push({ message: 'plain', durationMs: 0 });
  let snap = [];
  toastStore.subscribe((t) => { snap = t; });
  assert.equal(snap[0].kind, 'info');
});

test('subscribers receive a snapshot copy (mutation does not affect store)', () => {
  toastStore.push({ message: 'first', durationMs: 0 });
  let leaked = null;
  toastStore.subscribe((t) => { leaked = t; });
  leaked.push({ id: 'fake', message: 'sneaked in' });
  let canonical = [];
  toastStore.subscribe((t) => { canonical = t; });
  // The "leaked" mutation must NOT have affected internal state.
  assert.equal(canonical.length, 1);
});

test('unsubscribe stops receiving updates', () => {
  const seen = [];
  const unsub = toastStore.subscribe((t) => seen.push(t.length));
  unsub();
  toastStore.push({ message: 'after unsub', durationMs: 0 });
  // Only the initial-snapshot 0.
  assert.deepEqual(seen, [0]);
});

test('a thrown error in one subscriber does not break others', () => {
  const seen = [];
  toastStore.subscribe(() => { throw new Error('boom'); });
  toastStore.subscribe((t) => seen.push(t.length));
  toastStore.push({ message: 'go', durationMs: 0 });
  // The second subscriber still received the update.
  assert.deepEqual(seen, [0, 1]);
});

test('multiple toasts get unique increasing ids', () => {
  const id1 = toastStore.push({ message: '1', durationMs: 0 });
  const id2 = toastStore.push({ message: '2', durationMs: 0 });
  assert.notEqual(id1, id2);
  assert.ok(id2 > id1);
});

test('toasts are appended in order (FIFO render)', () => {
  toastStore.push({ message: 'first', durationMs: 0 });
  toastStore.push({ message: 'second', durationMs: 0 });
  toastStore.push({ message: 'third', durationMs: 0 });
  let snap = [];
  toastStore.subscribe((t) => { snap = t; });
  assert.deepEqual(snap.map((t) => t.message), ['first', 'second', 'third']);
});

test('toast.dismiss + toast.clear forward to the store', () => {
  const id = toast.show({ message: 'go', durationMs: 0 });
  toast.dismiss(id);
  let snap = [];
  toastStore.subscribe((t) => { snap = t; });
  assert.equal(snap.length, 0);
});

test('durationMs > 0 schedules an auto-dismiss', async () => {
  const id = toastStore.push({ message: 'soon', durationMs: 10 });
  await new Promise((r) => setTimeout(r, 30));
  let snap = [];
  toastStore.subscribe((t) => { snap = t; });
  // Auto-dismissed.
  assert.equal(snap.find((t) => t.id === id), undefined);
});

// The review loop's remembered open / closed rounds: per task, latest loop
// only, on the shared bounded store. localStorage is shimmed as the other
// memory tests do.
import assert from 'node:assert/strict';
import test, { beforeEach } from 'node:test';

import {
  forgetLoopExpansion,
  loopExpansion,
  rememberLoopExpansion,
} from './reviewLoopExpandMemory.js';

const KEY = 'kato.reviewLoopExpanded.v1';
let stored = {};

beforeEach(() => {
  stored = {};
  globalThis.localStorage = {
    getItem: (key) => (key in stored ? stored[key] : null),
    setItem: (key, value) => { stored[key] = String(value); },
    removeItem: (key) => { delete stored[key]; },
  };
});

test('nothing remembered reads as nothing open', () => {
  assert.deepEqual(loopExpansion('T-1', 'loop-a'), { rounds: {}, selfChecks: {} });
  assert.deepEqual(loopExpansion('T-1', ''), { rounds: {}, selfChecks: {} });
});

test('a loop gets back what was opened and closed, both kinds kept apart', () => {
  rememberLoopExpansion('T-1', 'loop-a', { rounds: { 1: false, 2: true } });
  rememberLoopExpansion('T-1', 'loop-a', { selfChecks: { 1: true } });
  assert.deepEqual(loopExpansion('T-1', 'loop-a'), {
    rounds: { 1: false, 2: true }, selfChecks: { 1: true },
  });
  assert.equal(JSON.parse(stored[KEY])['T-1'].loopId, 'loop-a');
});

test('another loop on the same task never sees the old choices — and replaces them', () => {
  rememberLoopExpansion('T-1', 'loop-a', { rounds: { 1: true } });
  assert.deepEqual(loopExpansion('T-1', 'loop-b'), { rounds: {}, selfChecks: {} });
  rememberLoopExpansion('T-1', 'loop-b', { rounds: { 2: true } });
  assert.deepEqual(loopExpansion('T-1', 'loop-a'), { rounds: {}, selfChecks: {} });
  assert.deepEqual(loopExpansion('T-1', 'loop-b').rounds, { 2: true });
});

test('Run again forgets the last loop; tasks are kept apart', () => {
  rememberLoopExpansion('T-1', 'loop-a', { rounds: { 1: true } });
  rememberLoopExpansion('T-2', 'loop-c', { rounds: { 3: true } });
  forgetLoopExpansion('T-1');
  assert.deepEqual(loopExpansion('T-1', 'loop-a'), { rounds: {}, selfChecks: {} });
  assert.deepEqual(loopExpansion('T-2', 'loop-c').rounds, { 3: true });
});

test('no loop id writes nothing; junk in storage reads as nothing', () => {
  rememberLoopExpansion('T-1', '', { rounds: { 1: true } });
  assert.equal(stored[KEY], undefined);
  stored[KEY] = JSON.stringify({ 'T-1': { loopId: 'loop-a', rounds: ['x'], selfChecks: { 1: 'yes', 2: false } } });
  assert.deepEqual(loopExpansion('T-1', 'loop-a'), { rounds: {}, selfChecks: { 2: false } });
});

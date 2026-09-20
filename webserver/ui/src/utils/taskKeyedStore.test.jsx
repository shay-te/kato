// The bounded per-task map both Files-pane memories are built on.
//
// Its consumers (``taskRepoMemory``, ``repoCollapseMemory``) each test their
// own entry shape. What is tested here is what they share and must not
// re-answer differently: the keying, the cap, and the isolation between two
// stores that live in the same localStorage.

import { describe, test, expect, beforeEach } from 'vitest';
import { createTaskKeyedStore } from './taskKeyedStore.js';

beforeEach(() => { localStorage.clear(); });

describe('createTaskKeyedStore', () => {
  test('round-trips an entry of any shape', () => {
    const store = createTaskKeyedStore('k.test.v1');
    store.writeEntry('T1', { anything: ['a', 'b'], nested: { n: 1 } });

    expect(store.readEntry('T1')).toMatchObject({
      anything: ['a', 'b'], nested: { n: 1 },
    });
  });

  test('stamps each write so the cap can tell old from new', () => {
    const store = createTaskKeyedStore('k.test.v1');
    store.writeEntry('T1', { v: 1 });

    expect(typeof store.readEntry('T1').at).toBe('number');
  });

  test('a later write replaces the entry rather than merging it', () => {
    const store = createTaskKeyedStore('k.test.v1');
    store.writeEntry('T1', { a: 1 });
    store.writeEntry('T1', { b: 2 });

    expect(store.readEntry('T1').a).toBeUndefined();
    expect(store.readEntry('T1').b).toBe(2);
  });

  test('two stores keep separate storage keys', () => {
    // Both Files-pane memories key by the same task ids. If they shared a
    // key, remembering a collapse would erase the remembered repo list.
    const repos = createTaskKeyedStore('k.repos.v1');
    const collapse = createTaskKeyedStore('k.collapse.v1');
    repos.writeEntry('T1', { repos: ['client'] });
    collapse.writeEntry('T1', { collapsed: ['backend'] });

    expect(repos.readEntry('T1').repos).toEqual(['client']);
    expect(collapse.readEntry('T1').collapsed).toEqual(['backend']);
  });

  test('a blank or missing task id neither reads nor writes', () => {
    const store = createTaskKeyedStore('k.test.v1');
    store.writeEntry('', { v: 1 });
    store.writeEntry('   ', { v: 1 });
    store.writeEntry(null, { v: 1 });

    expect(localStorage.getItem('k.test.v1')).toBeNull();
    expect(store.readEntry('')).toBeUndefined();
    expect(store.readEntry(null)).toBeUndefined();
  });

  test('ids are trimmed, so the same task is one entry', () => {
    const store = createTaskKeyedStore('k.test.v1');
    store.writeEntry('  T1  ', { v: 1 });

    expect(store.readEntry('T1').v).toBe(1);
    expect(Object.keys(JSON.parse(localStorage.getItem('k.test.v1')))).toEqual(['T1']);
  });

  test('drops the oldest written entries once past the cap', () => {
    const store = createTaskKeyedStore('k.test.v1', { maxTasks: 3 });
    for (let i = 0; i < 5; i += 1) {
      store.writeEntry(`T${i}`, { v: i });
    }

    expect(Object.keys(JSON.parse(localStorage.getItem('k.test.v1'))).sort())
      .toEqual(['T2', 'T3', 'T4']);
  });

  test('re-writing a task keeps it alive through the cap', () => {
    // Freshness is by last WRITE, not by first: the task being worked in
    // must not be evicted by tasks merely visited since.
    const store = createTaskKeyedStore('k.test.v1', { maxTasks: 2 });
    store.writeEntry('old', { v: 1 });
    store.writeEntry('mid', { v: 2 });
    store.writeEntry('old', { v: 3 });
    store.writeEntry('new', { v: 4 });

    expect(store.readEntry('old').v).toBe(3);
    expect(store.readEntry('new').v).toBe(4);
    expect(store.readEntry('mid')).toBeUndefined();
  });

  test('corrupt storage reads as empty instead of throwing', () => {
    const store = createTaskKeyedStore('k.test.v1');

    localStorage.setItem('k.test.v1', '{not json');
    expect(store.readEntry('T1')).toBeUndefined();

    localStorage.setItem('k.test.v1', '[1,2]');
    expect(store.readEntry('T1')).toBeUndefined();

    localStorage.setItem('k.test.v1', 'null');
    expect(store.readEntry('T1')).toBeUndefined();
  });

  test('a write over corrupt storage recovers rather than compounding', () => {
    const store = createTaskKeyedStore('k.test.v1');
    localStorage.setItem('k.test.v1', '{not json');
    store.writeEntry('T1', { v: 1 });

    expect(store.readEntry('T1').v).toBe(1);
  });
});

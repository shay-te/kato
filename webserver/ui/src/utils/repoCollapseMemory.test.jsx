// Remembering which repos an operator folded away, per task.
//
// Collapse used to be component state: switching task and back, or reloading,
// re-expanded everything. On a 26-repo task that means scrolling past all the
// noise you had just folded away.

import { describe, test, expect, beforeEach } from 'vitest';
import {
  collapsedRepos, rememberCollapsedRepos,
} from './repoCollapseMemory.js';

beforeEach(() => { localStorage.clear(); });

describe('repoCollapseMemory', () => {
  test('round-trips what was collapsed', () => {
    rememberCollapsedRepos('T1', new Set(['client', 'backend']));

    expect(collapsedRepos('T1').sort()).toEqual(['backend', 'client']);
  });

  test('a task nobody collapsed anything in starts fully expanded', () => {
    expect(collapsedRepos('never-seen')).toEqual([]);
    expect(collapsedRepos('')).toEqual([]);
  });

  test('expanding everything is remembered too', () => {
    // "I expanded them all" is a choice. Skipping the empty set would quietly
    // restore the previous collapse on the next visit.
    rememberCollapsedRepos('T1', new Set(['client']));
    rememberCollapsedRepos('T1', new Set());

    expect(collapsedRepos('T1')).toEqual([]);
  });

  test('a later write replaces the set rather than merging it', () => {
    rememberCollapsedRepos('T1', new Set(['client', 'backend']));
    rememberCollapsedRepos('T1', new Set(['client']));

    expect(collapsedRepos('T1')).toEqual(['client']);
  });

  test('tasks do not leak into each other', () => {
    // The same repo is the point of one task and noise in another.
    rememberCollapsedRepos('T1', new Set(['client']));
    rememberCollapsedRepos('T2', new Set(['backend']));

    expect(collapsedRepos('T1')).toEqual(['client']);
    expect(collapsedRepos('T2')).toEqual(['backend']);
  });

  test('an array is accepted as well as a Set', () => {
    rememberCollapsedRepos('T1', ['client']);

    expect(collapsedRepos('T1')).toEqual(['client']);
  });

  test('a blank task id is never written', () => {
    rememberCollapsedRepos('', new Set(['client']));
    rememberCollapsedRepos('   ', new Set(['client']));

    expect(localStorage.getItem('kato.repoCollapse.v1')).toBeNull();
  });

  test('entries are capped, oldest written dropped first', () => {
    for (let i = 0; i < 55; i += 1) {
      rememberCollapsedRepos(`T${i}`, new Set([`r${i}`]));
    }
    const stored = JSON.parse(localStorage.getItem('kato.repoCollapse.v1'));

    expect(Object.keys(stored)).toHaveLength(50);
    // The newest survive; the first writes are gone.
    expect(collapsedRepos('T54')).toEqual(['r54']);
    expect(collapsedRepos('T0')).toEqual([]);
  });

  test('a corrupt entry reads as nothing collapsed', () => {
    // Hand-edited or truncated storage must not take the pane down.
    localStorage.setItem('kato.repoCollapse.v1', '{not json');
    expect(collapsedRepos('T1')).toEqual([]);

    localStorage.setItem('kato.repoCollapse.v1', '[1,2]');
    expect(collapsedRepos('T1')).toEqual([]);

    localStorage.setItem('kato.repoCollapse.v1', '{"T1":{"collapsed":"nope"}}');
    expect(collapsedRepos('T1')).toEqual([]);
  });
});

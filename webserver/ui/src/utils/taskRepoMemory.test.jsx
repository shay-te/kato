// Remembering a task's repo list so the Files pane can draw its headers
// before the tree walk returns.

import { describe, test, expect, beforeEach } from 'vitest';
import {
  forgetRepos, rememberRepos, rememberedRepos,
} from './taskRepoMemory.js';

beforeEach(() => { localStorage.clear(); });

const TREES = [
  { repo_id: 'client', cwd: '/ws/client', branch: 'UNA-1' },
  { repo_id: 'backend', cwd: '/ws/backend', branch: 'UNA-1' },
];

describe('taskRepoMemory', () => {
  test('round-trips the repos a fetch returned', () => {
    rememberRepos('T1', TREES);
    const repos = rememberedRepos('T1');
    expect(repos.map((r) => r.repo_id)).toEqual(['client', 'backend']);
    expect(repos[0].branch).toBe('UNA-1');
  });

  test('an unknown task remembers nothing', () => {
    expect(rememberedRepos('nope')).toEqual([]);
    expect(rememberedRepos('')).toEqual([]);
  });

  test('an EMPTY result never erases what we knew', () => {
    // A failed or half-provisioned fetch looks exactly like "no repos", and
    // forgetting on it would blank the pane on the next switch — undoing the
    // whole point.
    rememberRepos('T1', TREES);
    rememberRepos('T1', []);
    expect(rememberedRepos('T1')).toHaveLength(2);
  });

  test('a later fetch replaces the list rather than merging it', () => {
    // A repo removed from the task must not linger as a ghost header.
    rememberRepos('T1', TREES);
    rememberRepos('T1', [{ repo_id: 'client', cwd: '/ws/client', branch: 'x' }]);
    expect(rememberedRepos('T1').map((r) => r.repo_id)).toEqual(['client']);
  });

  test('tasks do not leak into each other', () => {
    rememberRepos('T1', TREES);
    rememberRepos('T2', [{ repo_id: 'other', cwd: '/ws/other' }]);
    expect(rememberedRepos('T1')).toHaveLength(2);
    expect(rememberedRepos('T2').map((r) => r.repo_id)).toEqual(['other']);
  });

  test('forget drops one task only', () => {
    rememberRepos('T1', TREES);
    rememberRepos('T2', TREES);
    forgetRepos('T1');
    expect(rememberedRepos('T1')).toEqual([]);
    expect(rememberedRepos('T2')).toHaveLength(2);
  });

  test('entries are capped, oldest first', () => {
    for (let i = 0; i < 55; i += 1) {
      rememberRepos(`T${i}`, [{ repo_id: `r${i}`, cwd: `/ws/${i}` }]);
    }
    // The earliest writes are gone; the most recent survive.
    expect(rememberedRepos('T0')).toEqual([]);
    expect(rememberedRepos('T54')).toHaveLength(1);
  });

  test('a corrupt or unavailable store degrades to remembering nothing', () => {
    localStorage.setItem('kato.taskRepos.v1', 'not json{');
    expect(rememberedRepos('T1')).toEqual([]);
    // ...and a write over the corrupt value still works.
    expect(() => rememberRepos('T1', TREES)).not.toThrow();
    expect(rememberedRepos('T1')).toHaveLength(2);
  });

  test('garbage entries are filtered out, not rendered as blank repos', () => {
    localStorage.setItem('kato.taskRepos.v1', JSON.stringify({
      T1: { repos: [null, {}, { repo_id: 'real', cwd: '/ws/real' }], at: 1 },
    }));
    expect(rememberedRepos('T1').map((r) => r.repo_id)).toEqual(['real']);
  });
});

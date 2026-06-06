// Vitest (not node:test) because promptStore transitively imports a
// .md?raw default, which only Vite's pipeline resolves.
import { describe, test, expect, beforeEach } from 'vitest';

import { promptStore, EDITABLE_PROMPTS } from './promptStore.js';

const DEFAULT = EDITABLE_PROMPTS.find((p) => p.id === 'codeReview').default;

beforeEach(() => { promptStore.reset('codeReview'); });


describe('promptStore', () => {
  test('get() returns the shipped default when no override is set', () => {
    expect(promptStore.get('codeReview')).toBe(DEFAULT);
    expect(promptStore.isCustom('codeReview')).toBe(false);
  });

  test('setOverride() makes get() return the custom text + flags custom', () => {
    promptStore.setOverride('codeReview', 'my own review prompt');
    expect(promptStore.get('codeReview')).toBe('my own review prompt');
    expect(promptStore.isCustom('codeReview')).toBe(true);
    expect(promptStore.override('codeReview')).toBe('my own review prompt');
  });

  test('a blank/whitespace override resets to the default', () => {
    promptStore.setOverride('codeReview', 'x');
    promptStore.setOverride('codeReview', '   ');
    expect(promptStore.get('codeReview')).toBe(DEFAULT);
    expect(promptStore.isCustom('codeReview')).toBe(false);
  });

  test('reset() drops the override back to default', () => {
    promptStore.setOverride('codeReview', 'custom');
    promptStore.reset('codeReview');
    expect(promptStore.get('codeReview')).toBe(DEFAULT);
  });

  test('subscribe fires on change; no emit when value is unchanged', () => {
    const seen = [];
    const unsub = promptStore.subscribe((s) => seen.push(s));
    promptStore.setOverride('codeReview', 'a');
    promptStore.setOverride('codeReview', 'a'); // unchanged → no emit
    unsub();
    promptStore.setOverride('codeReview', 'b'); // after unsub → not seen
    expect(seen.length).toBe(2); // initial fire + one real change
  });

  test('unknown id resolves to empty default, not a crash', () => {
    expect(promptStore.get('nope')).toBe('');
  });
});

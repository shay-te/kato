// Vitest (not node:test) because promptStore transitively imports a
// .md?raw default, which only Vite's pipeline resolves.
import { describe, test, expect, beforeEach, vi } from 'vitest';

import {
  BUILTIN_PROMPTS,
  FAST_PROMPTS_STORAGE_KEY,
  LEGACY_PROMPT_OVERRIDES_STORAGE_KEY,
  promptStore,
} from './promptStore.js';

const DEFAULT = BUILTIN_PROMPTS.find((p) => p.id === 'codeReview').text;
const REVIEW = { label: 'Code review', icon: 'diff' };
const review = () => promptStore.list().find((p) => p.id === 'codeReview');

beforeEach(() => {
  promptStore.reset('codeReview');
  for (const prompt of promptStore.list()) {
    if (!prompt.builtin) { promptStore.remove(prompt.id); }
  }
});


describe('promptStore', () => {
  test('get() returns the shipped default when nothing is saved', () => {
    expect(promptStore.get('codeReview')).toBe(DEFAULT);
    expect(review()).toMatchObject({ ...REVIEW, builtin: true, changed: false });
  });

  test('save() makes get() return the custom text + flags the prompt changed', () => {
    promptStore.save('codeReview', { ...REVIEW, text: 'my own review prompt' });
    expect(promptStore.get('codeReview')).toBe('my own review prompt');
    expect(review().changed).toBe(true);
  });

  test('a shipped prompt can be renamed and given another icon', () => {
    promptStore.save('codeReview', { label: 'Review', icon: 'eye', text: DEFAULT });
    expect(review()).toMatchObject({ label: 'Review', icon: 'eye', text: DEFAULT, changed: true });
  });

  test('a blank name or text is refused, not saved', () => {
    expect(promptStore.save('codeReview', { ...REVIEW, text: '   ' })).toBe(false);
    expect(promptStore.save('codeReview', { ...REVIEW, label: '  ', text: 'x' })).toBe(false);
    expect(promptStore.get('codeReview')).toBe(DEFAULT);
    expect(review().changed).toBe(false);
  });

  test('saving the default back is a reset', () => {
    promptStore.save('codeReview', { ...REVIEW, text: 'custom' });
    promptStore.save('codeReview', { ...REVIEW, text: DEFAULT });
    expect(review().changed).toBe(false);
  });

  test('reset() drops every change back to the default', () => {
    promptStore.save('codeReview', { label: 'Review', icon: 'eye', text: 'custom' });
    promptStore.reset('codeReview');
    expect(review()).toMatchObject({ ...REVIEW, text: DEFAULT, changed: false });
  });

  test('an unknown icon keeps the one the prompt has', () => {
    promptStore.save('codeReview', { ...REVIEW, icon: 'not-an-icon', text: 'custom' });
    expect(review().icon).toBe('diff');
  });

  test('subscribe fires on change; no emit when value is unchanged', () => {
    const seen = [];
    const unsub = promptStore.subscribe((s) => seen.push(s));
    promptStore.save('codeReview', { ...REVIEW, text: 'a' });
    promptStore.save('codeReview', { ...REVIEW, text: 'a' }); // unchanged → no emit
    unsub();
    promptStore.save('codeReview', { ...REVIEW, text: 'b' }); // after unsub → not seen
    expect(seen.length).toBe(2); // initial fire + one real change
  });

  test('unknown id resolves to empty text, not a crash', () => {
    expect(promptStore.get('nope')).toBe('');
    expect(promptStore.save('nope', { ...REVIEW, text: 'x' })).toBe(false);
  });

  test('setIcon() applies on its own, without a save', () => {
    // Reported twice as "i can't change the prompt icon": picking an icon is
    // one click with a visible result, so it must not wait behind Save.
    expect(promptStore.setIcon('codeReview', 'eye')).toBe(true);
    expect(review()).toMatchObject({ icon: 'eye', label: 'Code review' });
    expect(promptStore.get('codeReview')).toBe(DEFAULT);
  });

  test('setIcon() refuses an unknown icon or an unknown prompt', () => {
    expect(promptStore.setIcon('codeReview', 'not-an-icon')).toBe(false);
    expect(promptStore.setIcon('nope', 'eye')).toBe(false);
    expect(review().icon).toBe('diff');
  });

  test('list() is the same array until something changes', () => {
    const before = promptStore.list();
    expect(promptStore.list()).toBe(before);
    promptStore.save('codeReview', { ...REVIEW, text: 'changed' });
    expect(promptStore.list()).not.toBe(before);
  });
});


describe('promptStore — the operator\'s own prompts', () => {
  const GO = { label: 'Go', icon: 'send', text: 'Go ahead.' };

  test('add() puts a prompt after the shipped ones, and get() sends its text', () => {
    const id = promptStore.add({ label: 'Explain the diff', icon: 'eye', text: 'Explain what changed.' });
    expect(id).toMatch(/^custom-/);
    expect(promptStore.list().map((p) => p.id)).toEqual(['codeReview', id]);
    expect(promptStore.get(id)).toBe('Explain what changed.');
  });

  test('a new prompt without a known icon gets the send icon', () => {
    const id = promptStore.add({ label: 'Go', icon: 'nope', text: 'Go ahead.' });
    expect(promptStore.list().find((p) => p.id === id).icon).toBe('send');
  });

  test('a prompt with a blank name or text is not added', () => {
    expect(promptStore.add({ ...GO, label: ' ' })).toBe('');
    expect(promptStore.add({ ...GO, text: ' ' })).toBe('');
    expect(promptStore.list()).toHaveLength(1);
  });

  test('save() edits an added prompt', () => {
    const id = promptStore.add(GO);
    expect(promptStore.save(id, { label: 'Ship it', icon: 'check', text: 'Ship it.' })).toBe(true);
    expect(promptStore.list().find((p) => p.id === id))
      .toMatchObject({ label: 'Ship it', icon: 'check', text: 'Ship it.', builtin: false });
  });

  test('remove() deletes an added prompt, never a shipped one', () => {
    const id = promptStore.add(GO);
    expect(promptStore.remove('codeReview')).toBe(false);
    expect(promptStore.remove(id)).toBe(true);
    expect(promptStore.list().map((p) => p.id)).toEqual(['codeReview']);
  });

  test('added prompts are saved in this browser', () => {
    const id = promptStore.add(GO);
    const saved = JSON.parse(window.localStorage.getItem(FAST_PROMPTS_STORAGE_KEY));
    expect(saved.custom).toEqual([{ id, ...GO }]);
  });
});


describe('promptStore — reading what an older version saved', () => {
  async function freshStore() {
    vi.resetModules();
    return (await import('./promptStore.js')).promptStore;
  }

  test('a review prompt saved before prompts could be added is kept', async () => {
    window.localStorage.removeItem(FAST_PROMPTS_STORAGE_KEY);
    window.localStorage.setItem(
      LEGACY_PROMPT_OVERRIDES_STORAGE_KEY, JSON.stringify({ codeReview: 'MY OLD REVIEW' }),
    );
    const store = await freshStore();
    window.localStorage.removeItem(LEGACY_PROMPT_OVERRIDES_STORAGE_KEY);
    expect(store.get('codeReview')).toBe('MY OLD REVIEW');
  });

  test('an unreadable saved list falls back to the shipped prompts', async () => {
    window.localStorage.setItem(FAST_PROMPTS_STORAGE_KEY, '{not json');
    const store = await freshStore();
    window.localStorage.removeItem(FAST_PROMPTS_STORAGE_KEY);
    expect(store.list().map((p) => p.id)).toEqual(['codeReview']);
    expect(store.get('codeReview')).toBe(DEFAULT);
  });

  test('a saved prompt with no text is dropped, not shown as an empty button', async () => {
    window.localStorage.setItem(FAST_PROMPTS_STORAGE_KEY, JSON.stringify({
      builtins: {},
      custom: [
        { id: 'custom-a', label: 'Empty', icon: 'send', text: '' },
        { id: 'custom-b', label: 'Kept', icon: 'eye', text: 'Hi.' },
      ],
    }));
    const store = await freshStore();
    window.localStorage.removeItem(FAST_PROMPTS_STORAGE_KEY);
    expect(store.list().map((p) => p.id)).toEqual(['codeReview', 'custom-b']);
  });
});

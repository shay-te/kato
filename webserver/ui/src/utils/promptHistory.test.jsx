import { describe, test, expect, beforeEach } from 'vitest';
import {
  readPromptHistory,
  rememberPrompt,
  forgetPromptHistory,
} from './promptHistory.js';

describe('promptHistory', () => {
  beforeEach(() => { window.localStorage.clear(); });

  test('returns empty for an unknown task', () => {
    expect(readPromptHistory('T-1')).toEqual([]);
    expect(readPromptHistory('')).toEqual([]);
  });

  test('remembers newest first', () => {
    rememberPrompt('T-1', 'first');
    rememberPrompt('T-1', 'second');
    expect(readPromptHistory('T-1')).toEqual(['second', 'first']);
  });

  test('collapses a repeat instead of making you press up twice', () => {
    rememberPrompt('T-1', 'same');
    rememberPrompt('T-1', 'same');
    expect(readPromptHistory('T-1')).toEqual(['same']);
  });

  test('re-sending an older prompt moves it to the front, not duplicates it', () => {
    rememberPrompt('T-1', 'a');
    rememberPrompt('T-1', 'b');
    rememberPrompt('T-1', 'a');
    expect(readPromptHistory('T-1')).toEqual(['a', 'b']);
  });

  test('ignores blank prompts', () => {
    rememberPrompt('T-1', '   ');
    expect(readPromptHistory('T-1')).toEqual([]);
  });

  test('history is per task — one task never recalls another\'s prompt', () => {
    rememberPrompt('T-1', 'mine');
    rememberPrompt('T-2', 'theirs');
    expect(readPromptHistory('T-1')).toEqual(['mine']);
    expect(readPromptHistory('T-2')).toEqual(['theirs']);
  });

  test('caps growth', () => {
    for (let i = 0; i < 60; i += 1) { rememberPrompt('T-1', `p${i}`); }
    expect(readPromptHistory('T-1')).toHaveLength(50);
    expect(readPromptHistory('T-1')[0]).toBe('p59');
  });

  test('survives corrupt storage', () => {
    window.localStorage.setItem('kato.promptHistory.v1.T-1', '{not json');
    expect(readPromptHistory('T-1')).toEqual([]);
  });

  test('forget clears one task only', () => {
    rememberPrompt('T-1', 'a');
    rememberPrompt('T-2', 'b');
    forgetPromptHistory('T-1');
    expect(readPromptHistory('T-1')).toEqual([]);
    expect(readPromptHistory('T-2')).toEqual(['b']);
  });
});

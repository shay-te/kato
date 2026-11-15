import assert from 'node:assert/strict';
import test from 'node:test';

import {
  CLAUDE_STATE,
  allChecksPass,
  claudeState,
  claudeUsable,
  homeRelative,
  initialFolder,
  repositoriesText,
  startBlocker,
} from './quickStart.js';

const READY = { claude: { installed: true, logged_in: true }, git: { installed: true } };

test('claude is missing, signed out, ready, or installed with no word on sign-in', () => {
  assert.equal(claudeState(null), CLAUDE_STATE.MISSING);
  assert.equal(claudeState({ installed: false }), CLAUDE_STATE.MISSING);
  assert.equal(claudeState({ installed: true, logged_in: false }), CLAUDE_STATE.SIGNED_OUT);
  assert.equal(claudeState({ installed: true, logged_in: true }), CLAUDE_STATE.READY);
  assert.equal(claudeState({ installed: true, logged_in: null }), CLAUDE_STATE.INSTALLED);
  // "cannot tell" is never read as signed out
  assert.equal(claudeUsable({ installed: true, logged_in: null }), true);
  assert.equal(claudeUsable({ installed: true, logged_in: false }), false);
});

test('a folder reads the way people say it', () => {
  assert.equal(homeRelative('/Users/me/dev', '/Users/me'), '~/dev');
  assert.equal(homeRelative('/Users/me', '/Users/me/'), '~');
  assert.equal(homeRelative('/Users/meow/dev', '/Users/me'), '/Users/meow/dev');
  assert.equal(homeRelative('/opt/code', '/Users/me'), '/opt/code');
  assert.equal(homeRelative('C:\\Users\\me\\dev', 'C:\\Users\\me'), '~\\dev');
  assert.equal(homeRelative('/x', ''), '/x');
  assert.equal(homeRelative(null, '/Users/me'), '');
  assert.equal(repositoriesText(1), '1 repository');
  assert.equal(repositoriesText(14), '14 repositories');
});

test('start picks the folder already set, else the best found, else the one to create', () => {
  assert.deepEqual(initialFolder({ folder: '/set', folders: [{ path: '/a' }] }), { path: '/set', create: false });
  assert.deepEqual(initialFolder({ folder: '', folders: [{ path: '/a' }, { path: '/b' }] }), { path: '/a', create: false });
  assert.deepEqual(initialFolder({ folders: [], new_folder: '/home/code' }), { path: '/home/code', create: true });
  assert.deepEqual(initialFolder(null), { path: '', create: true });
});

test('start says the first thing still missing, and nothing once all is there', () => {
  assert.equal(startBlocker(null, { path: '/a' }), 'Checking this computer…');
  assert.equal(startBlocker({ claude: { installed: false }, git: { installed: false } }, {}), 'Install Claude Code first.');
  assert.equal(startBlocker({ claude: { installed: true, logged_in: false }, git: { installed: true } }, { path: '/a' }), 'Sign in to Claude first.');
  assert.equal(startBlocker({ ...READY, git: { installed: false } }, { path: '/a' }), 'Install git first.');
  assert.equal(startBlocker(READY, { path: '  ' }), 'Pick the folder your code is in.');
  assert.equal(startBlocker(READY, { path: '/a' }), '');
  assert.equal(allChecksPass(READY), true);
  assert.equal(allChecksPass({ ...READY, git: {} }), false);
  assert.equal(allChecksPass(null), false);
});

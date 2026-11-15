import assert from 'node:assert/strict';
import test from 'node:test';

import {
  AGENT_STATE,
  QUICK_START_AGENTS,
  agentLabel,
  agentState,
  agentUsable,
  allChecksPass,
  homeRelative,
  initialFolder,
  repositoriesText,
  startBlocker,
} from './quickStart.js';

const READY = { agent: { installed: true, logged_in: true }, git: { installed: true } };

test('the four agents, in tab order, by the names people know', () => {
  assert.deepEqual(QUICK_START_AGENTS.map((agent) => agent.id), ['claude', 'codex', 'grok', 'opencode']);
  assert.equal(agentLabel('claude'), 'Claude Code');
  assert.equal(agentLabel('opencode'), 'OpenCode');
  assert.equal(agentLabel('mystery'), 'mystery');
  assert.equal(agentLabel(null), '');
});

test('an agent is missing, signed out, ready, or installed with no word on sign-in', () => {
  assert.equal(agentState(null), AGENT_STATE.MISSING);
  assert.equal(agentState({ installed: false }), AGENT_STATE.MISSING);
  assert.equal(agentState({ installed: true, logged_in: false }), AGENT_STATE.SIGNED_OUT);
  assert.equal(agentState({ installed: true, logged_in: true }), AGENT_STATE.READY);
  assert.equal(agentState({ installed: true, logged_in: null }), AGENT_STATE.INSTALLED);
  // "cannot tell" (every agent but Claude Code) is never read as signed out
  assert.equal(agentUsable({ installed: true, logged_in: null }), true);
  assert.equal(agentUsable({ installed: true, logged_in: false }), false);
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

test('no folder is picked for the operator — only one they chose before', () => {
  assert.deepEqual(initialFolder({ folder: '/set', folders: [{ path: '/a' }] }), { path: '/set', create: false });
  assert.equal(initialFolder({ folder: '', folders: [{ path: '/a' }] }), null);
  assert.equal(initialFolder({ folders: [], new_folder: '/home/code' }), null);
  assert.equal(initialFolder(null), null);
});

test('start says the first thing still missing, in the picked agent\'s name', () => {
  assert.equal(startBlocker({ agent: '', checks: null, folder: { path: '/a' } }), 'Pick the agent you use.');
  assert.equal(startBlocker({ agent: 'codex', checks: null, folder: { path: '/a' } }), 'Checking this computer…');
  assert.equal(startBlocker({ agent: 'codex', checks: { agent: { installed: false }, git: { installed: true } }, folder: null }),
    'Install Codex first.');
  assert.equal(startBlocker({ agent: 'claude', checks: { agent: { installed: true, logged_in: false }, git: { installed: true } }, folder: { path: '/a' } }),
    'Sign in to Claude Code first.');
  assert.equal(startBlocker({ agent: 'grok', checks: { ...READY, git: { installed: false } }, folder: { path: '/a' } }), 'Install git first.');
  assert.equal(startBlocker({ agent: 'grok', checks: READY, folder: null }), 'Pick the folder your code is in.');
  assert.equal(startBlocker({ agent: 'grok', checks: READY, folder: { path: '/a' } }), '');
  assert.equal(allChecksPass(READY), true);
  assert.equal(allChecksPass({ ...READY, git: {} }), false);
  assert.equal(allChecksPass(null), false);
});

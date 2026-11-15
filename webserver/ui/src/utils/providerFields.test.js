import assert from 'node:assert/strict';
import test from 'node:test';

import {
  connectionFixes, isSecretKey, isWorkflowField, buildDraftFor, orderedFieldKeys, orderedProviders, providerOptionLabel,
} from './providerFields.js';


test('isSecretKey: true for token/secret/password keys (case-insensitive)', () => {
  assert.equal(isSecretKey('API_TOKEN'), true);
  assert.equal(isSecretKey('client_secret'), true);
  assert.equal(isSecretKey('Password'), true);
  assert.equal(isSecretKey('app_password_hash'), true);
});

test('isSecretKey: false for non-secret keys', () => {
  assert.equal(isSecretKey('host'), false);
  assert.equal(isSecretKey('username'), false);
  assert.equal(isSecretKey('base_url'), false);
});

test('buildDraftFor: maps each field key to its current value', () => {
  const providers = {
    bitbucket: { fields: { user: { value: 'me' }, token: { value: 't' } } },
  };
  assert.deepEqual(buildDraftFor(providers, 'bitbucket'), { user: 'me', token: 't' });
});

test('buildDraftFor: empty string for missing values; empty object for unknown provider', () => {
  const providers = { github: { fields: { token: {} } } };
  assert.deepEqual(buildDraftFor(providers, 'github'), { token: '' });
  assert.deepEqual(buildDraftFor(providers, 'nope'), {});
  assert.deepEqual(buildDraftFor(null, 'github'), {});
});


test('orderedProviders: the hosts the operator set up come first, the rest keep their order', () => {
  const supported = ['bitbucket', 'github', 'gitlab', 'azure'];
  const providers = { azure: { set_up: true }, github: { set_up: true }, bitbucket: { set_up: false } };
  assert.deepEqual(orderedProviders(supported, providers), ['github', 'azure', 'bitbucket', 'gitlab']);
  // A server that does not say keeps its own order.
  assert.deepEqual(orderedProviders(supported, {}), supported);
  assert.deepEqual(orderedProviders(null, providers), []);
});

test('providerOptionLabel: says whether each host is set up, only when the server says', () => {
  const labels = { azure: 'Azure DevOps', bitbucket: 'Bitbucket' };
  const providers = { azure: { set_up: true }, bitbucket: { set_up: false } };
  assert.equal(providerOptionLabel('azure', labels, providers), 'Azure DevOps — set up');
  assert.equal(providerOptionLabel('bitbucket', labels, providers), 'Bitbucket — not set up');
  assert.equal(providerOptionLabel('gitlab', labels, providers), 'gitlab');
});

test('isWorkflowField: the start / review / done state fields, nothing else', () => {
  for (const key of ['JIRA_PROGRESS_STATE', 'JIRA_PROGRESS_STATE_FIELD', 'YOUTRACK_REVIEW_STATE', 'GITHUB_DONE_STATE_FIELD']) {
    assert.equal(isWorkflowField(key), true, key);
  }
  for (const key of ['JIRA_ISSUE_STATES', 'JIRA_API_TOKEN', 'JIRA_ASSIGNEE', '', null]) {
    assert.equal(isWorkflowField(key), false, String(key));
  }
});

test('orderedFieldKeys: the server order first, the rest after, unknown names dropped', () => {
  const keys = ['JIRA_API_TOKEN', 'JIRA_ASSIGNEE', 'JIRA_DONE_STATE', 'JIRA_EMAIL', 'EXTRA'];
  assert.deepEqual(
    orderedFieldKeys(['JIRA_EMAIL', 'JIRA_API_TOKEN', 'NOT_A_FIELD', 'JIRA_ASSIGNEE', 'JIRA_DONE_STATE'], keys),
    ['JIRA_EMAIL', 'JIRA_API_TOKEN', 'JIRA_ASSIGNEE', 'JIRA_DONE_STATE', 'EXTRA'],
  );
  assert.deepEqual(orderedFieldKeys(undefined, ['b', 'a']), ['b', 'a']);
  assert.deepEqual(orderedFieldKeys(['a'], undefined), []);
});

test('connectionFixes: the gateway address kato named becomes a field to fill in, for Jira only', () => {
  const said = 'Jira refused this token … Set the API base URL to https://api.atlassian.com/ex/jira/07cc605d-3220-485b-8a81-3b3f4742f64d — the same email';
  assert.deepEqual(connectionFixes('jira', said), [{
    key: 'JIRA_API_BASE_URL', value: 'https://api.atlassian.com/ex/jira/07cc605d-3220-485b-8a81-3b3f4742f64d', label: 'Use this address',
  }]);
  assert.deepEqual(connectionFixes('jira', 'HTTP 401'), []);
  assert.deepEqual(connectionFixes('youtrack', said), []);
  assert.deepEqual(connectionFixes('jira', null), []);
});

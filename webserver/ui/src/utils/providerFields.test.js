import assert from 'node:assert/strict';
import test from 'node:test';

import {
  isSecretKey, buildDraftFor, orderedProviders, providerOptionLabel,
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

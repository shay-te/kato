// Tests for the credential-source field rules shared by every credential
// form. These key names mirror the server (credential_sources.py); a drift
// here silently stops the operator's choice from persisting.
import { describe, test } from 'node:test';
import assert from 'node:assert/strict';
import {
  credentialKeysFor,
  isCredentialSourceKey,
  usesDiscoveredCredential,
} from './credentialSourceFields.js';

describe('credentialKeysFor', () => {
  test('derives both key names from the provider id', () => {
    assert.deepEqual(credentialKeysFor('github'), {
      token: 'GITHUB_API_TOKEN', source: 'GITHUB_API_TOKEN_SOURCE',
    });
    assert.deepEqual(credentialKeysFor(' BitBucket '), {
      token: 'BITBUCKET_API_TOKEN', source: 'BITBUCKET_API_TOKEN_SOURCE',
    });
  });

  test('no provider yields no keys (nothing to hide or require)', () => {
    assert.deepEqual(credentialKeysFor(''), { token: '', source: '' });
    assert.deepEqual(credentialKeysFor(undefined), { token: '', source: '' });
  });
});

describe('isCredentialSourceKey', () => {
  test('matches the picker-owned key only', () => {
    assert.ok(isCredentialSourceKey('GITHUB_API_TOKEN_SOURCE'));
    assert.ok(!isCredentialSourceKey('GITHUB_API_TOKEN'));
    assert.ok(!isCredentialSourceKey('GITHUB_API_BASE_URL'));
    assert.ok(!isCredentialSourceKey(''));
  });
});

describe('usesDiscoveredCredential', () => {
  test('a discovered source means no token input', () => {
    assert.ok(usesDiscoveredCredential('cli'));
    assert.ok(usesDiscoveredCredential('git-credential'));
    assert.ok(usesDiscoveredCredential('environment'));
  });

  test('pasting, or nothing chosen yet, keeps the token input', () => {
    assert.ok(!usesDiscoveredCredential('pasted'));
    assert.ok(!usesDiscoveredCredential(''));
    assert.ok(!usesDiscoveredCredential(undefined));
    assert.ok(!usesDiscoveredCredential('   '));
  });
});

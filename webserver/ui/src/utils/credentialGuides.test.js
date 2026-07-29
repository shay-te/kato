// Tests for the credential guides — the "why kato needs this key and where
// you get it" catalog behind the wizard/settings cards. The install that
// produced this file stalled on GitHub ("there is no SECURITY menu"), so the
// menu path and the doc links are the parts worth pinning.
import { describe, test } from 'node:test';
import assert from 'node:assert/strict';
import {
  CREDENTIAL_GUIDES,
  credentialGuideFor,
  credentialGuideForKey,
  credentialLocationForKey,
} from './credentialGuides.js';
import { fieldInfo } from './fieldHelp.js';

// Every provider/agent the setup wizard can ask a credential for.
const ASKED_FOR_CREDENTIALS = [
  'youtrack', 'jira', 'github', 'gitlab', 'bitbucket',
  'claude', 'openhands', 'openrouter', 'bedrock',
];

describe('CREDENTIAL_GUIDES', () => {
  test('covers every platform kato asks a key for', () => {
    for (const id of ASKED_FOR_CREDENTIALS) {
      assert.ok(CREDENTIAL_GUIDES[id], `missing guide for ${id}`);
    }
  });

  test('every guide explains WHY the key is needed, in prose', () => {
    for (const [id, guide] of Object.entries(CREDENTIAL_GUIDES)) {
      assert.ok(guide.why.length > 40, `${id}: why is too thin`);
      assert.match(guide.why, /kato|agent/i,
        `${id}: why should say what kato/the agent does with it`);
    }
  });

  test('every guide has steps, a menu path and at least one link', () => {
    for (const [id, guide] of Object.entries(CREDENTIAL_GUIDES)) {
      assert.ok(guide.steps.length >= 3, `${id}: not enough steps`);
      assert.ok(guide.location, `${id}: no menu path`);
      assert.ok(guide.docsUrl || guide.createUrl, `${id}: no link`);
    }
  });

  test('every link is an https URL with a label', () => {
    for (const [id, guide] of Object.entries(CREDENTIAL_GUIDES)) {
      if (guide.docsUrl) {
        assert.match(guide.docsUrl, /^https:\/\//, `${id}: docsUrl`);
        assert.ok(guide.docsLabel, `${id}: docsUrl without a label`);
      }
      if (guide.createUrl) {
        assert.match(guide.createUrl, /^https:\/\//, `${id}: createUrl`);
        assert.ok(guide.createLabel, `${id}: createUrl without a label`);
      }
    }
  });

  test('GitHub points at Developer settings, not a Security menu', () => {
    const github = CREDENTIAL_GUIDES.github;
    assert.ok(github.location.includes('Developer settings'));
    assert.ok(github.steps.some((step) => step.includes('Developer settings')));
    // The exact wrong turn the first operator took.
    assert.ok(!/Security menu/i.test(github.location));
  });

  test('Claude is flagged as nothing-to-paste', () => {
    assert.equal(CREDENTIAL_GUIDES.claude.storesSecret, false);
  });
});

describe('credentialGuideFor', () => {
  test('resolves by provider id, case-insensitively', () => {
    assert.equal(credentialGuideFor('github').provider, 'GitHub');
    assert.equal(credentialGuideFor('GitHub').provider, 'GitHub');
    assert.equal(credentialGuideFor(' jira ').provider, 'Jira');
  });

  test('unknown / empty ids return null so callers render nothing', () => {
    assert.equal(credentialGuideFor('codex'), null);
    assert.equal(credentialGuideFor(''), null);
    assert.equal(credentialGuideFor(undefined), null);
  });
});

describe('credentialGuideForKey', () => {
  test('maps a settings key to its provider guide by prefix', () => {
    assert.equal(credentialGuideForKey('GITHUB_API_TOKEN').provider, 'GitHub');
    assert.equal(credentialGuideForKey('BITBUCKET_API_TOKEN').provider, 'Bitbucket');
    assert.equal(credentialGuideForKey('REPOSITORY_ROOT_PATH'), null);
  });

  test('credentialLocationForKey degrades to empty, never throws', () => {
    assert.ok(credentialLocationForKey('GITLAB_API_TOKEN').includes('Access tokens'));
    assert.equal(credentialLocationForKey('KATO_SOMETHING_ODD'), '');
  });
});

describe('fieldInfo uses the guide as its single source of truth', () => {
  test('the token tooltip carries the real menu path', () => {
    assert.ok(fieldInfo('GITHUB_API_TOKEN').includes('Developer settings'));
    assert.ok(fieldInfo('YOUTRACK_API_TOKEN').includes('Account Security'));
  });

  test('a token key with no guide still reads sensibly', () => {
    const info = fieldInfo('ACME_API_TOKEN');
    assert.ok(info.includes('account settings'));
    assert.ok(info.endsWith('Environment variable: ACME_API_TOKEN'));
  });
});

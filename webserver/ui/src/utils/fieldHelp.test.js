// Tests for the shared field metadata: friendly labels, example
// placeholders, and info text that carries the env-var name (its only
// home in the UI — labels never print raw keys).
import { describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { humanizeFieldKey, fieldPlaceholder, fieldInfo } from './fieldHelp.js';

describe('humanizeFieldKey', () => {
  test('drops the platform prefix and prettifies acronyms', () => {
    assert.equal(humanizeFieldKey('JIRA_API_BASE_URL', 'jira'), 'API base URL');
    assert.equal(humanizeFieldKey('YOUTRACK_ASSIGNEE', 'youtrack'), 'Assignee');
    assert.equal(humanizeFieldKey('BITBUCKET_REPO_SLUG', 'bitbucket'), 'Repo slug');
  });

  test('keys without the prefix still render readably', () => {
    assert.equal(humanizeFieldKey('REPOSITORY_ROOT_PATH', 'jira'), 'Repository root path');
  });
});

describe('fieldPlaceholder', () => {
  test('platform-aware URL examples', () => {
    assert.equal(fieldPlaceholder('YOUTRACK_API_BASE_URL'), 'https://yourcompany.youtrack.cloud/api');
    assert.equal(fieldPlaceholder('JIRA_API_BASE_URL'), 'https://your-domain.atlassian.net');
    assert.equal(fieldPlaceholder('GITLAB_API_BASE_URL'), 'https://gitlab.com/api/v4');
  });

  test('workflow-state examples for every platform', () => {
    assert.equal(fieldPlaceholder('YOUTRACK_REVIEW_STATE'), 'To Verify');
    assert.equal(fieldPlaceholder('GITHUB_ISSUE_STATES'), 'Open,To Do');
    assert.equal(fieldPlaceholder('BITBUCKET_PROGRESS_STATE'), 'In Progress');
  });

  test('gitlab projects use the group/project shape', () => {
    assert.equal(fieldPlaceholder('GITLAB_PROJECT'), 'group/project');
    assert.equal(fieldPlaceholder('JIRA_PROJECT'), 'PROJ');
  });

  test('unknown keys fall back to empty (caller may know better)', () => {
    assert.equal(fieldPlaceholder('KATO_SOMETHING_ODD'), '');
  });
});

describe('fieldInfo', () => {
  test('always ends with the environment variable name', () => {
    const info = fieldInfo('YOUTRACK_API_TOKEN');
    assert.ok(info.includes('security settings'));
    assert.ok(info.endsWith('Environment variable: YOUTRACK_API_TOKEN'));
  });

  test('extra caller text is included before the env-var line', () => {
    const info = fieldInfo('REPOSITORY_ROOT_PATH', 'Extra tip.');
    assert.ok(info.includes('Extra tip.'));
    assert.ok(info.endsWith('Environment variable: REPOSITORY_ROOT_PATH'));
  });
});

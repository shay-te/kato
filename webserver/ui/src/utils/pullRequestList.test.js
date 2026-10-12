// The header's pull-request list, in words.
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  pullRequestBadge,
  pullRequestCounts,
  pullRequestLabel,
  pullRequestListTooltip,
  pullRequestTone,
  pullRequestUrls,
} from './pullRequestList.js';

const pr = (id, url = `https://host/pr/${id}`) => ({ id, title: '', url });
const repo = (repositoryId, pullRequest = null, lookupFailed = false) => ({ repositoryId, pullRequest, lookupFailed });
const MIXED = [repo('api', pr('1')), repo('web'), repo('lib', null, true)];

test('counts the open pull requests, of how many repositories, and the unchecked', () => {
  assert.deepEqual(pullRequestCounts(MIXED), { open: 1, total: 3, unchecked: 1 });
  assert.deepEqual(pullRequestCounts(null), { open: 0, total: 0, unchecked: 0 });
});

test('the badge appears once any pull request is open', () => {
  assert.equal(pullRequestBadge(MIXED), '1/3');
  assert.equal(pullRequestBadge([repo('api')]), '');
  assert.equal(pullRequestBadge([]), '');
});

test('done only when every repository has its pull request', () => {
  assert.equal(pullRequestTone([repo('api', pr('1')), repo('web', pr('2'))]), 'good');
  assert.equal(pullRequestTone(MIXED), 'warn');
});

test('the tooltip says how they stand', () => {
  assert.equal(pullRequestListTooltip(MIXED),
    'Pull requests open in 1 of 3 repositories; 1 could not be checked. '
    + 'Click for the list — open or copy each one.');
  assert.equal(pullRequestListTooltip([repo('api')]),
    'Pull requests open in 0 of 1 repository. Click for the list — open or copy each one.');
  assert.equal(pullRequestListTooltip([]), 'Pull requests — no repositories on this task to check yet.');
});

test('a pull request is named by its number and title', () => {
  assert.equal(pullRequestLabel({ id: '42', title: 'Add login' }), '#42 Add login');
  assert.equal(pullRequestLabel({ id: '42', title: '' }), '#42');
  assert.equal(pullRequestLabel({ id: '', title: 'Add login' }), 'Add login');
  assert.equal(pullRequestLabel(null), 'Pull request');
});

test('"open all" opens the links there are, in order', () => {
  assert.deepEqual(pullRequestUrls([repo('a', pr('1')), repo('b'), repo('c', pr('3', ''))]), ['https://host/pr/1']);
  assert.deepEqual(pullRequestUrls(null), []);
});

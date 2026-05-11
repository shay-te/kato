import assert from 'node:assert/strict';
import test from 'node:test';

import { formatRelativeTime } from './relativeTime.js';


test('formatRelativeTime under one minute returns just now', function () {
  assert.equal(formatRelativeTime(0), 'just now');
  assert.equal(formatRelativeTime(30), 'just now');
  assert.equal(formatRelativeTime(59), 'just now');
});

test('formatRelativeTime under one hour returns minutes', function () {
  assert.equal(formatRelativeTime(60), '1m ago');
  assert.equal(formatRelativeTime(125), '2m ago');
  assert.equal(formatRelativeTime(3599), '59m ago');
});

test('formatRelativeTime under one day returns hours', function () {
  assert.equal(formatRelativeTime(3600), '1h ago');
  assert.equal(formatRelativeTime(7200), '2h ago');
  assert.equal(formatRelativeTime(86399), '23h ago');
});

test('formatRelativeTime over one day returns days', function () {
  assert.equal(formatRelativeTime(86400), '1d ago');
  assert.equal(formatRelativeTime(86400 * 3), '3d ago');
});

test('formatRelativeTime returns dash for non-finite or negative input', function () {
  assert.equal(formatRelativeTime(-1), '—');
  assert.equal(formatRelativeTime(NaN), '—');
  assert.equal(formatRelativeTime(Infinity), '—');
});

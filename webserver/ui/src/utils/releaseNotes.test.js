import assert from 'node:assert/strict';
import test from 'node:test';

import { googleCopyLine, releaseNotesForDisplay } from './releaseNotes.js';

test('the invisible task markers are stripped, the line is kept', () => {
  const md = '- 2026-10-21 — Clients can set a password (UNA-1) <!-- kato:task UNA-1 -->\n';
  assert.equal(releaseNotesForDisplay(md), '- 2026-10-21 — Clients can set a password (UNA-1)\n');
  assert.equal(releaseNotesForDisplay(null), '');
});

test('other comments the operator wrote are left alone', () => {
  assert.equal(releaseNotesForDisplay('<!-- my note -->'), '<!-- my note -->');
});

test('the Google Doc line: nothing when off, red on a failure, else when it was copied', () => {
  const now = Date.parse('2026-10-21T12:00:00Z');
  assert.deepEqual(googleCopyLine({ wanted: false }), { text: '', tone: '' });
  assert.deepEqual(googleCopyLine(undefined), { text: '', tone: '' });
  assert.deepEqual(
    googleCopyLine({ wanted: true, error: 'share the Doc' }),
    { text: 'Google Doc copy failed: share the Doc', tone: 'error' },
  );
  assert.equal(googleCopyLine({ wanted: true, last_synced_at: 0 }).text, 'Not copied to the Google Doc yet.');
  const at = (ms) => ({ wanted: true, last_synced_at: (now - ms) / 1000 });
  assert.equal(googleCopyLine(at(10_000), now).text, 'Copied to the Google Doc just now.');
  assert.equal(googleCopyLine(at(5 * 60_000), now).text, 'Copied to the Google Doc 5m ago.');
  assert.equal(googleCopyLine(at(3 * 3_600_000), now).text, 'Copied to the Google Doc 3h ago.');
  assert.equal(googleCopyLine(at(3 * 86_400_000), now).text, 'Copied to the Google Doc on 2026-10-18.');
});

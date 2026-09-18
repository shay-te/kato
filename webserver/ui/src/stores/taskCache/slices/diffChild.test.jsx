// Wiring test for the diff slice: the changeset on screen is offered back to
// the server as its ETag, so an unchanged poll costs nothing.
//
// This is the heaviest payload the app polls — one 27-repository task measured
// 4.3 MB, rebuilt every five seconds and almost always identical to what the
// pane was already showing. What this proves is that the slice sends the tag,
// and that a 304 keeps the SAME parsed data (so the memoised diff pane and the
// file-tree badges re-render nothing). Drives the real chain: diffChild →
// createDataStore → parseRepoDiffs, with only the network edge replaced.

import { describe, test, expect, beforeEach, vi } from 'vitest';

const { _api } = vi.hoisted(() => ({ _api: { responses: [], calls: [] } }));

vi.mock('../../../api.js', () => ({
  fetchDiff: async (taskId, options = {}) => {
    _api.calls.push({ taskId, signature: options.signature || '' });
    return _api.responses.length > 1 ? _api.responses.shift() : _api.responses[0];
  },
}));

const { diffChild } = await import('./diffChild.js');

const flush = () => new Promise((r) => setTimeout(r, 0));

const payloadWith = (repoId) => ({
  repository_ids: [repoId],
  diffs: [{
    repo_id: repoId,
    cwd: `/w/${repoId}`,
    conflicted_files: [],
    diff: [
      'diff --git a/src/app.py b/src/app.py',
      'index 1111111..2222222 100644',
      '--- a/src/app.py',
      '+++ b/src/app.py',
      '@@ -1 +1 @@',
      '-old',
      '+new',
      '',
    ].join('\n'),
  }],
});
const built = (repoId, etag = `"${repoId}"`) => ({ payload: payloadWith(repoId), etag });
const unchanged = (etag) => ({ unchanged: true, etag });

beforeEach(() => {
  diffChild.clear();
  _api.responses = [];
  _api.calls = [];
});

describe('diffChild — an unchanged changeset costs nothing', () => {
  test('a first load has no tag to offer and parses what arrives', async () => {
    _api.responses = [built('client', '"etag-1"')];

    await diffChild.load('T1');
    await flush();

    expect(_api.calls).toEqual([{ taskId: 'T1', signature: '' }]);
    expect(diffChild.get('T1').status).toBe('ready');
    expect(diffChild.get('T1').data.length).toBe(1);
  });

  test('the changeset on screen is offered back as its ETag', async () => {
    _api.responses = [built('client', '"etag-1"')];
    await diffChild.load('T1');
    await flush();

    await diffChild.load('T1');
    await flush();

    expect(_api.calls.map((call) => call.signature)).toEqual(['', '"etag-1"']);
  });

  test('a 304 keeps the SAME parsed data — nothing re-parsed, nothing re-rendered', async () => {
    _api.responses = [built('client', '"etag-1"')];
    await diffChild.load('T1');
    await flush();
    const painted = diffChild.get('T1').data;

    _api.responses = [unchanged('"etag-1"')];
    await diffChild.load('T1');
    await flush();

    // Referential identity is the point: the diff pane and the tree badges
    // both memoise on this reference.
    expect(diffChild.get('T1').data).toBe(painted);
    expect(diffChild.get('T1').status).toBe('ready');
  });

  test('a changed changeset replaces what is on screen', async () => {
    _api.responses = [built('client', '"etag-1"')];
    await diffChild.load('T1');
    await flush();
    const painted = diffChild.get('T1').data;

    _api.responses = [built('backend', '"etag-2"')];
    await diffChild.load('T1');
    await flush();

    expect(diffChild.get('T1').data).not.toBe(painted);
  });

  test('a purged task offers no tag — it holds nothing to confirm', async () => {
    // A signature outliving its data would earn a 304 for a task holding
    // nothing, and the pane would sit empty until something else changed.
    _api.responses = [built('client', '"etag-1"')];
    await diffChild.load('T1');
    await flush();
    diffChild.purge('T1');

    _api.responses = [built('client', '"etag-1"')];
    await diffChild.load('T1');
    await flush();

    expect(_api.calls[_api.calls.length - 1].signature).toBe('');
    expect(diffChild.get('T1').data.length).toBe(1);
  });

  test('a server that sends no ETag still works, by comparing the payload', async () => {
    _api.responses = [built('client', '')];
    await diffChild.load('T1');
    await flush();
    const painted = diffChild.get('T1').data;

    _api.responses = [built('client', '')];
    await diffChild.load('T1');
    await flush();

    expect(diffChild.get('T1').data).toBe(painted);
  });
});

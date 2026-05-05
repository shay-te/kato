import assert from 'node:assert/strict';
import test, { afterEach } from 'node:test';

import { adoptClaudeSession, fetchClaudeSessions } from './api.js';


function _stubFetch(response) {
  const calls = [];
  globalThis.fetch = function (url, init) {
    calls.push({ url, init });
    return Promise.resolve(response);
  };
  return calls;
}

afterEach(function () {
  delete globalThis.fetch;
});


test('fetchClaudeSessions hits /api/claude/sessions with no query when empty', async function () {
  const calls = _stubFetch({
    ok: true,
    json: () => Promise.resolve({ sessions: [] }),
  });
  await fetchClaudeSessions('');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/claude/sessions');
});

test('fetchClaudeSessions URL-encodes the query string', async function () {
  const calls = _stubFetch({
    ok: true,
    json: () => Promise.resolve({ sessions: [] }),
  });
  await fetchClaudeSessions('auth flow');
  assert.equal(calls[0].url, '/api/claude/sessions?q=auth%20flow');
});

test('fetchClaudeSessions throws when the response is not ok', async function () {
  _stubFetch({
    ok: false,
    status: 500,
    statusText: 'Server Error',
    json: () => Promise.resolve({ error: 'storage corrupt' }),
  });
  await assert.rejects(
    () => fetchClaudeSessions(''),
    /storage corrupt/,
  );
});

test('adoptClaudeSession posts the session id as JSON', async function () {
  const calls = _stubFetch({
    ok: true,
    status: 200,
    json: () => Promise.resolve({ task_id: 'PROJ-1', claude_session_id: 'sess-1' }),
  });
  const result = await adoptClaudeSession('PROJ-1', 'sess-1');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/sessions/PROJ-1/adopt-claude-session');
  assert.equal(calls[0].init.method, 'POST');
  assert.equal(calls[0].init.headers['content-type'], 'application/json');
  assert.deepEqual(JSON.parse(calls[0].init.body), { claude_session_id: 'sess-1' });
  assert.equal(result.ok, true);
  assert.equal(result.body.claude_session_id, 'sess-1');
});

test('adoptClaudeSession returns ok=false without calling fetch when task_id is empty', async function () {
  const calls = _stubFetch({
    ok: true,
    json: () => Promise.resolve({}),
  });
  const result = await adoptClaudeSession('', 'sess-1');
  assert.equal(result.ok, false);
  assert.equal(calls.length, 0);
});

test('adoptClaudeSession returns ok=false without calling fetch when session id is empty', async function () {
  const calls = _stubFetch({
    ok: true,
    json: () => Promise.resolve({}),
  });
  const result = await adoptClaudeSession('PROJ-1', '');
  assert.equal(result.ok, false);
  assert.equal(calls.length, 0);
});

test('adoptClaudeSession surfaces backend error body when status is non-2xx', async function () {
  _stubFetch({
    ok: false,
    status: 409,
    json: () => Promise.resolve({ error: 'live session running' }),
  });
  const result = await adoptClaudeSession('PROJ-1', 'sess-1');
  assert.equal(result.ok, false);
  assert.equal(result.status, 409);
  assert.equal(result.body.error, 'live session running');
});

test('adoptClaudeSession URL-encodes the task id', async function () {
  const calls = _stubFetch({
    ok: true,
    status: 200,
    json: () => Promise.resolve({}),
  });
  await adoptClaudeSession('PROJ/1', 'sess-1');
  assert.equal(calls[0].url, '/api/sessions/PROJ%2F1/adopt-claude-session');
});

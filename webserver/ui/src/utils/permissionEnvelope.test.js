// Tests for ``unpackPermissionEnvelope`` — normalizes the two
// permission-event shapes (legacy flat ``permission_request`` and
// modern nested ``control_request``) into a single
// ``{requestId, toolName, toolInput}`` for the PermissionDecisionContainer.
//
// Surface previously untested; a bug here causes the UI to show
// "tool" (the default) or an empty request id, which makes
// allow / deny clicks no-ops (the response can't find the source
// request).

import assert from 'node:assert/strict';
import test from 'node:test';

import { unpackPermissionEnvelope } from './permissionEnvelope.js';


// ---------------------------------------------------------------------------
// Flat shape (legacy ``permission_request``)
// ---------------------------------------------------------------------------

test('unpack: flat shape with request_id + tool_name + input', function () {
  const result = unpackPermissionEnvelope({
    request_id: 'req-1',
    tool_name: 'Bash',
    input: { command: 'ls' },
  });
  assert.equal(result.requestId, 'req-1');
  assert.equal(result.toolName, 'Bash');
  assert.deepEqual(result.toolInput, { command: 'ls' });
});

test('unpack: flat shape with "id" instead of "request_id"', function () {
  // Older backends use ``id``. Both must resolve.
  const result = unpackPermissionEnvelope({
    id: 'req-2', tool: 'Write',
  });
  assert.equal(result.requestId, 'req-2');
  assert.equal(result.toolName, 'Write');
});


// ---------------------------------------------------------------------------
// Nested shape (modern ``control_request``)
// ---------------------------------------------------------------------------

test('unpack: nested under "request" key', function () {
  const result = unpackPermissionEnvelope({
    type: 'control_request',
    request: {
      request_id: 'req-3',
      tool_name: 'Edit',
      input: { file: '/tmp/x' },
    },
  });
  assert.equal(result.requestId, 'req-3');
  assert.equal(result.toolName, 'Edit');
  assert.deepEqual(result.toolInput, { file: '/tmp/x' });
});

test('unpack: top-level fields win over nested when both present', function () {
  // Two backends might both populate the field — the top-level is
  // canonical (closer to the wire format).
  const result = unpackPermissionEnvelope({
    request_id: 'top',
    request: { request_id: 'nested' },
  });
  assert.equal(result.requestId, 'top');
});

test('unpack: falls through to nested when top-level is empty/missing', function () {
  const result = unpackPermissionEnvelope({
    request: { request_id: 'nested-only', tool_name: 'Read' },
  });
  assert.equal(result.requestId, 'nested-only');
  assert.equal(result.toolName, 'Read');
});


// ---------------------------------------------------------------------------
// Defensive / weird inputs
// ---------------------------------------------------------------------------

test('unpack: null / undefined raw → safe defaults', function () {
  // Must not throw; UI is OK with empty id (the modal won't render
  // a "submit" path) but it must not crash.
  const r1 = unpackPermissionEnvelope(null);
  const r2 = unpackPermissionEnvelope(undefined);
  assert.equal(r1.requestId, '');
  assert.equal(r1.toolName, 'tool');  // documented default
  assert.deepEqual(r1.toolInput, {});
  assert.deepEqual(r2, r1);
});

test('unpack: raw.request === null does not crash', function () {
  // ``typeof null === 'object'`` is a famous JS quirk — the helper
  // guards against this by also checking truthiness.
  const result = unpackPermissionEnvelope({
    request_id: 'r1', request: null,
  });
  assert.equal(result.requestId, 'r1');
});

test('unpack: empty object → fallback toolName "tool"', function () {
  const result = unpackPermissionEnvelope({});
  assert.equal(result.requestId, '');
  assert.equal(result.toolName, 'tool');
  assert.deepEqual(result.toolInput, {});
});

test('unpack: coerces non-string ids to strings', function () {
  // Backends may sometimes serialize ids as numbers.
  const result = unpackPermissionEnvelope({ request_id: 42 });
  assert.equal(result.requestId, '42');
  assert.equal(typeof result.requestId, 'string');
});

test('unpack: handles missing input gracefully', function () {
  const result = unpackPermissionEnvelope({ request_id: 'r', tool: 'X' });
  assert.deepEqual(result.toolInput, {});
});

test('unpack: prefers tool_name over tool when both present', function () {
  // ``tool_name`` is the modern field; ``tool`` is the older alias.
  const result = unpackPermissionEnvelope({
    tool_name: 'NewName', tool: 'OldName',
  });
  assert.equal(result.toolName, 'NewName');
});

test('unpack: preserves rich input objects', function () {
  // The PermissionDecisionContainer passes ``input`` through to the
  // backend as ``updatedInput`` — every nested field matters.
  const input = {
    command: 'rm -rf /tmp/test',
    cwd: '/work',
    env: { NODE_ENV: 'production' },
    timeout_ms: 30000,
  };
  const result = unpackPermissionEnvelope({
    request_id: 'r', tool: 'Bash', input,
  });
  assert.deepEqual(result.toolInput, input);
});

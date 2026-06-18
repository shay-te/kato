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

import {
  unpackPermissionEnvelope,
  commandSignatureOf,
  decisionCommandFor,
  isHighRiskActionGuard,
} from './permissionEnvelope.js';


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

// ---------------------------------------------------------------------------
// commandSignatureOf — the remembered KEY is the program, not the verbatim
// (path/arg-specific) line, so the same command matches across task folders.
// ---------------------------------------------------------------------------

test('signature: bare program with args → just the program', function () {
  assert.equal(commandSignatureOf('mvn -B verify'), 'mvn');
  assert.equal(commandSignatureOf('ls -la src/test/java/com/una/x'), 'ls');
});

test('signature: strips the leading `cd <task-path> &&` so it matches across folders', function () {
  // The exact pain from the screenshot: every command was prefixed with a
  // task-specific `cd …/UNA-2727`, so no two ever matched.
  const a = commandSignatureOf('cd /Users/x/dev_kato/UNA-2727 && mvn -B verify');
  const b = commandSignatureOf('cd /Users/x/dev_kato/UNA-2742 && mvn -B verify');
  assert.equal(a, 'mvn');
  assert.equal(b, 'mvn');
  assert.equal(a, b); // different task folders → same remembered key
});

test('signature: strips leading env-var assignments and `export …`', function () {
  assert.equal(commandSignatureOf('JAVA_HOME=/x/y mvn verify'), 'mvn');
  assert.equal(commandSignatureOf('export JAVA_HOME=/x && mvn verify'), 'mvn');
});

test('signature: basename-only — path / ./ prefixes do not fork the key', function () {
  assert.equal(commandSignatureOf('/usr/local/bin/docker ps'), 'docker');
  assert.equal(commandSignatureOf('./gradlew build'), 'gradlew');
});

test('signature: a chain keeps EVERY program so a new one re-prompts', function () {
  // Safety: tacking `rm -rf` onto an allowed `mvn` must NOT ride the `mvn`
  // grant — the signatures differ, so the chained command asks again.
  assert.equal(commandSignatureOf('mvn verify && rm -rf target'), 'mvn rm');
  assert.notEqual(
    commandSignatureOf('mvn verify && rm -rf target'),
    commandSignatureOf('mvn verify'),
  );
});

test('signature: dedups repeats, keeps first-seen order', function () {
  assert.equal(commandSignatureOf('git add . && git commit -m x && git push'), 'git');
  assert.equal(commandSignatureOf('docker build . && mvn test && docker push'), 'docker mvn');
});

test('signature: pipes count as separate programs', function () {
  assert.equal(commandSignatureOf('cat log | grep ERROR'), 'cat grep');
});

test('signature: subshell wrappers resolve to the real program', function () {
  assert.equal(commandSignatureOf('(cd /x && mvn verify)'), 'mvn');
  assert.equal(commandSignatureOf('$(which mvn)'), 'which');
});

test('signature: navigation-only command keys on the navigation verb', function () {
  // Nothing meaningful after stripping noise → fall back to the noise verb
  // so a bare `cd` is still a real, clearable entry (never an empty key).
  assert.equal(commandSignatureOf('cd /Users/x/somewhere'), 'cd');
});

test('signature: empty / whitespace → empty', function () {
  assert.equal(commandSignatureOf(''), '');
  assert.equal(commandSignatureOf('   '), '');
  assert.equal(commandSignatureOf(null), '');
});

test('signature: a NON-empty command never collapses to an empty key (no tool-wide over-allow)', function () {
  // A command with no resolvable program (only env assignments / redirects)
  // must NOT yield '' — an empty signature would make a remembered Bash
  // decision key as the bare tool `Bash` = allow-all-bash. Falls back to the
  // whole normalized command so the grant stays specific.
  assert.equal(commandSignatureOf('FOO=bar'), 'FOO=bar');
  assert.equal(commandSignatureOf('FOO=bar BAZ=qux'), 'FOO=bar BAZ=qux');
  // The invariant that matters: a non-empty command is NEVER an empty key.
  for (const cmd of ['FOO=bar', 'FOO=bar BAZ=qux', 'X=1 Y=2 Z=3']) {
    assert.notEqual(commandSignatureOf(cmd), '', cmd);
    assert.notEqual(decisionCommandFor('Bash', { command: cmd }), '', cmd);
  }
});

test('decisionCommandFor: Bash keys on the signature, non-Bash stays tool-level', function () {
  assert.equal(
    decisionCommandFor('Bash', { command: 'cd /x/UNA-1 && mvn verify' }),
    'mvn',
  );
  assert.equal(decisionCommandFor('Edit', { file_path: '/x' }), '');
  assert.equal(decisionCommandFor('Bash', {}), '');
});

test('unpack: surfaces action_guard from the top level', function () {
  const result = unpackPermissionEnvelope({
    type: 'control_request', request_id: 'r', tool: 'Bash',
    input: { command: 'cat ~/.ssh/id_rsa' },
    action_guard: { category: 'credential_read', decision: 'block', reason: 'x' },
  });
  assert.equal(result.actionGuard.category, 'credential_read');
});

test('unpack: surfaces action_guard nested under request', function () {
  const result = unpackPermissionEnvelope({
    type: 'control_request', request_id: 'r',
    request: {
      tool_name: 'Bash', input: { command: 'x' },
      action_guard: { category: 'network_exfil', decision: 'block' },
    },
  });
  assert.equal(result.actionGuard.category, 'network_exfil');
});

test('unpack: actionGuard is null when absent (old envelopes unchanged)', function () {
  const result = unpackPermissionEnvelope({
    request_id: 'r', tool: 'Bash', input: { command: 'ls' },
  });
  assert.equal(result.actionGuard, null);
});

test('isHighRiskActionGuard: true for credential/exfil/rce/escape, false otherwise', function () {
  assert.equal(isHighRiskActionGuard({ category: 'credential_read' }), true);
  assert.equal(isHighRiskActionGuard({ category: 'network_exfil' }), true);
  assert.equal(isHighRiskActionGuard({ category: 'destructive_fs' }), false);
  assert.equal(isHighRiskActionGuard({ category: 'out_of_scope' }), false);
  // network_tool (WebFetch/WebSearch) is dual-use → NOT high-risk, so an
  // operator approval can be remembered (it used to re-block every call).
  assert.equal(isHighRiskActionGuard({ category: 'network_tool' }), false);
  assert.equal(isHighRiskActionGuard(null), false);
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

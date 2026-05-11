import assert from 'node:assert/strict';
import test from 'node:test';

import { formatToolUse } from './formatToolUse.js';


// ----- full path / command transparency (no truncation) -----

test('Read renders the full file path, not a shortened one', function () {
  const out = formatToolUse('Read', {
    file_path: '/Users/shay/.kato/workspaces/PROJ-1/client/src/utils/long/path/auth.py',
  });
  assert.equal(typeof out, 'string');
  assert.equal(
    out,
    'Read · /Users/shay/.kato/workspaces/PROJ-1/client/src/utils/long/path/auth.py',
  );
  assert.equal(out.includes('…'), false);
});

test('Bash renders the full command without truncation', function () {
  const longCmd = 'git log --oneline ' + 'origin/master..HEAD '.repeat(10);
  const out = formatToolUse('Bash', { command: longCmd });
  // No "…" appended even though the command is well past the
  // legacy 120-char cap.
  assert.equal(out.includes('…'), false);
  assert.equal(out.startsWith('$ git log --oneline'), true);
  assert.ok(out.length > 120);
});

test('Bash with multi-line command renders as summary + details', function () {
  const out = formatToolUse('Bash', {
    command: 'cat <<EOF > /tmp/foo\nhello\nworld\nEOF',
  });
  assert.equal(typeof out, 'object');
  assert.equal(out.summary, '$ cat <<EOF > /tmp/foo');
  assert.equal(out.details, 'hello\nworld\nEOF');
});

test('Grep / Glob with path show full path', function () {
  const grepOut = formatToolUse('Grep', {
    pattern: 'TODO',
    path: '/Users/shay/.kato/workspaces/PROJ-1/client/src',
  });
  assert.equal(
    grepOut,
    'Grep · "TODO" in /Users/shay/.kato/workspaces/PROJ-1/client/src',
  );
});


// ----- Edit / Write / MultiEdit emit structured {summary, details} -----

test('Edit shows a single-line replacement as -/+ pair plus a summary badge', function () {
  const out = formatToolUse('Edit', {
    file_path: '/repo/src/auth.py',
    old_string: 'return None',
    new_string: 'return verify(token)',
  });
  assert.equal(typeof out, 'object');
  assert.equal(out.summary, 'Edit · /repo/src/auth.py · +1 -1');
  assert.equal(out.details, '- return None\n+ return verify(token)');
});

test('Edit keeps unchanged lines as context (matches VS Code Claude\'s diff view)', function () {
  // Common prefix + middle change + common suffix → only the middle
  // line should be tagged as removed/added; everything else is
  // context. This mirrors how Claude Code's VS Code extension
  // renders Edit tool calls (small green/red region with the
  // surrounding code shown plain).
  const out = formatToolUse('Edit', {
    file_path: '/repo/foo.js',
    old_string: 'const a = 1\nconst b = 2\nconst c = 3',
    new_string: 'const a = 1\nconst b = 22\nconst c = 3',
  });
  assert.equal(
    out.details,
    '  const a = 1\n- const b = 2\n+ const b = 22\n  const c = 3',
  );
  assert.equal(out.summary, 'Edit · /repo/foo.js · +1 -1');
});

test('Edit treats a pure insertion as added lines surrounded by context', function () {
  // The two new lines are inserted between the unchanged ones; the
  // diff should keep the surrounding lines as context (no -/+
  // duplication of every existing line).
  const out = formatToolUse('Edit', {
    file_path: '/repo/foo.js',
    old_string: 'name: "",\nisInitialized: false,\n})',
    new_string: 'name: "",\nisInitialized: false,\nisDragging: false,\ndragHoverPlusId: null,\n})',
  });
  assert.equal(
    out.details,
    '  name: "",\n  isInitialized: false,\n+ isDragging: false,\n+ dragHoverPlusId: null,\n  })',
  );
  assert.equal(out.summary, 'Edit · /repo/foo.js · +2');
});

test('Edit with empty old (insertion) renders only added lines', function () {
  const out = formatToolUse('Edit', {
    file_path: '/repo/foo.py',
    old_string: '',
    new_string: 'new line',
  });
  assert.equal(out.details, '+ new line');
  assert.equal(out.summary, 'Edit · /repo/foo.py · +1');
});

test('Edit drops a single trailing newline so we do not emit a stray prefix-only row', function () {
  const out = formatToolUse('Edit', {
    file_path: '/repo/foo.py',
    old_string: 'a\nb\n',
    new_string: 'x\ny\n',
  });
  // No common lines → all of old becomes -, all of new becomes +.
  assert.equal(out.details, '- a\n- b\n+ x\n+ y');
});

test('MultiEdit joins each edit block with a separator and aggregates the badge', function () {
  const out = formatToolUse('MultiEdit', {
    file_path: '/repo/foo.py',
    edits: [
      { old_string: 'a', new_string: 'A' },
      { old_string: 'b', new_string: 'B' },
    ],
  });
  assert.equal(out.summary, 'Edit · /repo/foo.py (2 edits) · +2 -2');
  assert.equal(out.details, '- a\n+ A\n---\n- b\n+ B');
});

test('Write emits the full content as +-prefixed details', function () {
  const out = formatToolUse('Write', {
    file_path: '/repo/new.py',
    content: 'def foo():\n    return 1',
  });
  assert.equal(out.summary, 'Write · /repo/new.py');
  assert.equal(out.details, '+ def foo():\n+     return 1');
});

test('Write with no content stays a header-only string', function () {
  const out = formatToolUse('Write', {
    file_path: '/repo/empty.py',
    content: '',
  });
  assert.equal(out, 'Write · /repo/empty.py');
});


// ----- TodoWrite expansion -----

test('TodoWrite renders every todo with a status marker', function () {
  const out = formatToolUse('TodoWrite', {
    todos: [
      { content: 'do thing one', status: 'completed' },
      { content: 'do thing two', status: 'in_progress' },
      { content: 'do thing three', status: 'pending' },
    ],
  });
  assert.equal(out.summary, 'TodoWrite · 3 items');
  assert.equal(
    out.details,
    '✓ do thing one\n→ do thing two\n· do thing three',
  );
});

test('TodoWrite with no items stays a string header', function () {
  const out = formatToolUse('TodoWrite', { todos: [] });
  assert.equal(out, 'TodoWrite · 0 items');
});


// ----- back-compat: simple tools still return strings -----

test('Glob without path returns a header-only string', function () {
  const out = formatToolUse('Glob', { pattern: '**/*.py' });
  assert.equal(out, 'Glob · **/*.py');
});

test('unknown tool falls back to compact JSON header', function () {
  const out = formatToolUse('SomeNewTool', { foo: 'bar' });
  assert.equal(typeof out, 'string');
  assert.ok(out.startsWith('SomeNewTool('));
});

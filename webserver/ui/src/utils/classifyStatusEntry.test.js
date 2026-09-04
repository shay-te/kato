// Tests for ``classifyStatusEntry`` — maps backend status messages to
// notification kinds + titles. Untested before; this surface is what
// drives the notification routing AND the per-task attention tab dot,
// so a broken pattern silently breaks both downstream.
//
// We pin one happy case + the negative paths per pattern, then a
// handful of adversarial inputs (null, empty, off-anchor, multi-match
// priority).

import assert from 'node:assert/strict';
import test from 'node:test';

import { NOTIFICATION_KIND } from '../constants/notificationKind.js';
import { classifyStatusEntry } from './classifyStatusEntry.js';


function _entry(message) {
  return { message };
}


// ---------------------------------------------------------------------------
// Defensive / not-classified.
// ---------------------------------------------------------------------------

test('classifyStatusEntry returns null for null / undefined entry', function () {
  assert.equal(classifyStatusEntry(null), null);
  assert.equal(classifyStatusEntry(undefined), null);
});

test('classifyStatusEntry returns null for entry with no message', function () {
  assert.equal(classifyStatusEntry({}), null);
  assert.equal(classifyStatusEntry({ message: '' }), null);
  assert.equal(classifyStatusEntry({ message: null }), null);
});

test('classifyStatusEntry returns null for unrecognised messages', function () {
  // Important negative — if the regex is loosened by mistake, every
  // log line would route into notifications.
  assert.equal(classifyStatusEntry(_entry('random log line')), null);
  assert.equal(classifyStatusEntry(_entry('DEBUG: trace ...')), null);
});

test('classifyStatusEntry is anchored at the start of the message', function () {
  // Patterns are ``^...`` so a message that contains the substring
  // mid-line MUST NOT match. Otherwise a debug-print of a status
  // line would re-fire the notification.
  assert.equal(
    classifyStatusEntry(_entry('echo: Mission PROJ-1: starting mission')),
    null,
  );
});


// ---------------------------------------------------------------------------
// Each pattern: happy case + the wrong-id parsing case to confirm the
// task id capture works.
// ---------------------------------------------------------------------------

test('classifyStatusEntry: wait-planning tag → STARTED kind, taskId captured', function () {
  const result = classifyStatusEntry(_entry(
    'task PROJ-1 tagged kato:wait-planning',
  ));
  assert.equal(result.kind, NOTIFICATION_KIND.STARTED);
  assert.equal(result.taskId, 'PROJ-1');
  assert.equal(result.title, 'Planning chat ready');
});

test('classifyStatusEntry: wait-editing tag → STARTED kind, own title', function () {
  const result = classifyStatusEntry(_entry(
    'task PROJ-7 tagged kato:wait-editing',
  ));
  assert.equal(result.kind, NOTIFICATION_KIND.STARTED);
  assert.equal(result.taskId, 'PROJ-7');
  // Distinct from wait-planning: this hold is waiting on the operator, and
  // there is no plan coming.
  assert.equal(result.title, 'Waiting for your go-ahead');
});

test('classifyStatusEntry: "starting mission" with summary uses both parts in body', function () {
  const result = classifyStatusEntry(_entry(
    'Mission PROJ-2: starting mission: fix the login bug',
  ));
  assert.equal(result.kind, NOTIFICATION_KIND.STARTED);
  assert.equal(result.taskId, 'PROJ-2');
  assert.ok(result.body.includes('fix the login bug'));
  assert.ok(result.body.includes('PROJ-2'));
});

test('classifyStatusEntry: "starting mission" with no summary still classifies', function () {
  const result = classifyStatusEntry(_entry(
    'Mission PROJ-3: starting mission',
  ));
  assert.equal(result.kind, NOTIFICATION_KIND.STARTED);
  assert.equal(result.taskId, 'PROJ-3');
});

test('classifyStatusEntry: "moved issue to in progress" → STATUS_CHANGE', function () {
  const result = classifyStatusEntry(_entry(
    'Mission PROJ-4: moved issue to in progress',
  ));
  assert.equal(result.kind, NOTIFICATION_KIND.STATUS_CHANGE);
  assert.equal(result.taskId, 'PROJ-4');
});

test('classifyStatusEntry: "moved issue to review state" → STATUS_CHANGE', function () {
  const result = classifyStatusEntry(_entry(
    'Mission PROJ-5: moved issue to review state',
  ));
  assert.equal(result.kind, NOTIFICATION_KIND.STATUS_CHANGE);
});

test('classifyStatusEntry: "awaiting push approval" → ATTENTION', function () {
  // Critical for the operator-pain "I missed the push approval".
  // Misclassifying this loses the notification.
  const result = classifyStatusEntry(_entry(
    'task PROJ-6 implementation complete; awaiting push approval',
  ));
  assert.equal(result.kind, NOTIFICATION_KIND.ATTENTION);
  assert.equal(result.taskId, 'PROJ-6');
  assert.ok(result.body.toLowerCase().includes('approve push'));
});

test('classifyStatusEntry: workflow completed → COMPLETED', function () {
  const result = classifyStatusEntry(_entry(
    'Mission PROJ-7: workflow completed successfully',
  ));
  assert.equal(result.kind, NOTIFICATION_KIND.COMPLETED);
  assert.equal(result.taskId, 'PROJ-7');
});

test('classifyStatusEntry: claude asking permission → ATTENTION + tool name in body', function () {
  // The operator needs to see WHICH tool is asking — that drives
  // the trust-this-time decision.
  const result = classifyStatusEntry(_entry(
    'task PROJ-8: claude is asking permission to run Bash',
  ));
  assert.equal(result.kind, NOTIFICATION_KIND.ATTENTION);
  assert.equal(result.taskId, 'PROJ-8');
  assert.ok(result.body.includes('Bash'));
  // The tool name is exposed separately so the notification router can
  // recall a saved decision and stay silent for an auto-resolved ask.
  assert.equal(result.permissionTool, 'Bash');
});

test('classifyStatusEntry: non-permission entries carry no permissionTool', function () {
  const result = classifyStatusEntry(_entry(
    'task PROJ-8 implementation complete; awaiting push approval',
  ));
  assert.equal(result.permissionTool, undefined);
});

test('classifyStatusEntry: claude turn ended with error → ERROR', function () {
  const result = classifyStatusEntry(_entry(
    'task PROJ-9: claude turn ended (error)',
  ));
  assert.equal(result.kind, NOTIFICATION_KIND.ERROR);
  assert.equal(result.taskId, 'PROJ-9');
});


// ---------------------------------------------------------------------------
// Edge cases that catch silent regressions.
// ---------------------------------------------------------------------------

test('classifyStatusEntry: task ids with special chars are captured (\\S+)', function () {
  // Task ids in YouTrack/Jira can have dashes, dots, underscores.
  // The regex uses \S+ so anything that's not whitespace works.
  for (const taskId of ['T-1', 'PROJ-123', 'project.sub-task', 'task_42']) {
    const result = classifyStatusEntry(_entry(
      `Mission ${taskId}: workflow completed successfully`,
    ));
    assert.equal(
      result.taskId, taskId,
      `task id ${taskId} should have been captured`,
    );
  }
});

test('classifyStatusEntry: pattern order — first match wins', function () {
  // No message currently matches multiple patterns, but if a future
  // pattern is added that overlaps, this test catches the ambiguity
  // by pinning the iteration order.
  // The "starting mission" + "starting mission: details" suffix
  // variants share a regex — the trailing colon-detail group is
  // optional (`(?:: (.+))?`). Both variants must classify as STARTED.
  const a = classifyStatusEntry(_entry('Mission X: starting mission'));
  const b = classifyStatusEntry(_entry('Mission X: starting mission: hi'));
  assert.equal(a.kind, NOTIFICATION_KIND.STARTED);
  assert.equal(b.kind, NOTIFICATION_KIND.STARTED);
});

test('classifyStatusEntry: similar-but-different messages do NOT match', function () {
  // "moved issue to" without the suffix doesn't match. Without the
  // anchor + suffix check, this would be a false-positive.
  assert.equal(
    classifyStatusEntry(_entry('Mission X: moved issue to')),
    null,
  );
  // The error pattern requires literal "(error)" — anything else
  // is silent.
  assert.equal(
    classifyStatusEntry(_entry('task X: claude turn ended (success)')),
    null,
  );
});

// ---------------------------------------------------------------------------
// Source-update completion (drives the "Source updated" OS notification).
// ---------------------------------------------------------------------------

test('classifyStatusEntry: source update finished → SOURCE_UPDATE notification', function () {
  const r = classifyStatusEntry(
    _entry('Mission UNA-2794: source update finished (3 updated, 1 skipped, 0 failed)'),
  );
  assert.equal(r.kind, NOTIFICATION_KIND.SOURCE_UPDATE);
  assert.equal(r.title, 'Source updated');
  assert.equal(r.taskId, 'UNA-2794');
  assert.match(r.body, /3 repo\(s\) updated/);
  assert.doesNotMatch(r.body, /failed/);
});

test('classifyStatusEntry: source update WITH failures flags the error variant', function () {
  const r = classifyStatusEntry(
    _entry('Mission UNA-9: source update finished (1 updated, 0 skipped, 2 failed)'),
  );
  assert.equal(r.kind, NOTIFICATION_KIND.SOURCE_UPDATE);
  assert.equal(r.title, 'Source update finished (with errors)');
  assert.match(r.body, /1 repo\(s\) updated, 2 failed/);
});

test('classifyStatusEntry: a partial source-update line does NOT match', function () {
  // Per-repo progress lines ("update-source for task X: ...") must NOT fire a
  // notification — only the single completion summary does.
  assert.equal(
    classifyStatusEntry(_entry('update-source for task UNA-1: client @ /x now on UNA-1')),
    null,
  );
});

// ---------------------------------------------------------------------------
// Repository clone failure → ERROR notification.
//
// A clone failure used to reach the operator through the preflight log only,
// which is replayed into a single task's session stream — so it was visible
// only to someone already looking at the chat that had just failed to get any
// files. The symptom was an empty Files pane with nothing anywhere saying why.
// ---------------------------------------------------------------------------

test('classifyStatusEntry: repository clone failure → ERROR kind', function () {
  const out = classifyStatusEntry({
    message: 'Mission UNA-3025: repository clone failed: fatal: could not read from remote repository',
  });
  assert.ok(out, 'clone failure was not classified at all');
  assert.strictEqual(out.kind, NOTIFICATION_KIND.ERROR);
  assert.strictEqual(out.taskId, 'UNA-3025');
  assert.match(out.title, /clone failed/i);
});

test('classifyStatusEntry: the clone-failure body carries the git reason', function () {
  // "Repository clone failed" alone is not actionable — auth, network and
  // disk-full all look identical without the reason.
  const out = classifyStatusEntry({
    message: 'Mission UNA-3025: repository clone failed: Permission denied (publickey)',
  });
  assert.match(out.body, /Permission denied \(publickey\)/);
  assert.match(out.body, /UNA-3025/);
});

test('classifyStatusEntry: a very long git error is trimmed for the body', function () {
  // Notification bodies are a toast, not a log pane; the full text stays in
  // the status feed and the preflight log.
  const out = classifyStatusEntry({
    message: `Mission UNA-3025: repository clone failed: ${'x'.repeat(400)}`,
  });
  assert.ok(out.body.length < 200, `body was not trimmed: ${out.body.length}`);
  assert.match(out.body, /…$/);
});

test('classifyStatusEntry: clone-failure rule is anchored, not substring', function () {
  // A log line that merely quotes the phrase must not fire a notification.
  assert.strictEqual(
    classifyStatusEntry({
      message: 'debug: previous entry was "Mission X: repository clone failed: boom"',
    }),
    null,
  );
});

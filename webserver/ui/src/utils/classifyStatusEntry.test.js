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

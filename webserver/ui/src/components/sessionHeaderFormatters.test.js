import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  formatFinishResult,
  formatMergeResult,
  formatMergeConflicts,
  formatPullResult,
  formatPushResult,
  formatPushSummary,
  formatRequestFailure,
  formatUpdateSourceResult,
} from './sessionHeaderFormatters.js';


test('formatMergeResult lists each merged repo with its commit count + branch', () => {
  const out = formatMergeResult({
    ok: true,
    body: {
      merged_repositories: [
        { repository_id: 'pii-core-lib', commits_merged: 3, default_branch: 'master' },
        { repository_id: 'task-core-lib', commits_merged: 1, default_branch: 'master' },
      ],
      skipped_repositories: [],
      failed_repositories: [],
    },
  });
  assert.equal(out.title, 'Default branch merged');
  assert.equal(out.kind, 'success');
  assert.match(out.message, /✓ pii-core-lib: merged 3 commit\(s\) from master/);
  assert.match(out.message, /✓ task-core-lib: merged 1 commit\(s\) from master/);
});

test('formatMergeResult includes the task id in the title when given', () => {
  // Regression: same class of bug as formatFinishResult/formatPushResult —
  // the merge toast named the repo but never the task.
  const out = formatMergeResult({
    ok: true,
    body: {
      merged_repositories: [
        { repository_id: 'ob-love-admin-client', commits_merged: 32, default_branch: 'master' },
      ],
      skipped_repositories: [],
      failed_repositories: [],
    },
  }, 'UNA-2536');
  assert.equal(out.title, 'Default branch merged (UNA-2536)');
});

test('formatMergeResult: nothing to merge LISTS the already-up-to-date repos', () => {
  const out = formatMergeResult({
    ok: true,
    body: {
      merged_repositories: [],
      skipped_repositories: [
        { repository_id: 'core-lib', reason: 'already_up_to_date' },
        { repository_id: 'llm-core-lib', reason: 'already_up_to_date' },
      ],
      failed_repositories: [],
    },
  });
  assert.equal(out.title, 'Nothing to merge');
  assert.equal(out.kind, 'info');
  assert.match(out.message, /• core-lib: already up to date/);
  assert.match(out.message, /• llm-core-lib: already up to date/);
});

test('formatMergeResult: a failure with no merges is an error toast', () => {
  const out = formatMergeResult({
    ok: true,
    body: {
      merged_repositories: [],
      skipped_repositories: [],
      failed_repositories: [{ repository_id: 'pay-core-lib', error: 'fetch refused' }],
    },
  });
  assert.equal(out.title, 'Merge failed');
  assert.equal(out.kind, 'error');
  assert.match(out.message, /✗ pay-core-lib: fetch refused/);
});

test('formatMergeResult: partial (some merged, some failed) downgrades to warning', () => {
  const out = formatMergeResult({
    ok: true,
    body: {
      merged_repositories: [{ repository_id: 'a', commits_merged: 2, default_branch: 'main' }],
      skipped_repositories: [{ repository_id: 'b', reason: 'already_up_to_date' }],
      failed_repositories: [{ repository_id: 'c', error: 'boom' }],
    },
  });
  assert.equal(out.title, 'Default branch merged (partial)');
  assert.equal(out.kind, 'warning');
  assert.match(out.message, /✓ a: merged 2 commit\(s\) from main/);
  assert.match(out.message, /• b: already up to date/);
  assert.match(out.message, /✗ c: boom/);
});

test('formatMergeResult surfaces a request failure', () => {
  const out = formatMergeResult({ ok: false, error: 'network' });
  assert.equal(out.kind, 'error');
  assert.match(out.message, /network/);
});

// ``/push`` returns the FLAT ``push_task`` payload: ``pushed`` is a
// boolean and the repo lists live at the top level of ``body`` (NOT
// nested under ``pushed`` like the finish toast). These tests pin that
// real shape — the previous nested-shape tests were the reason the
// "Pushed · push: no action" bug shipped (the backend never produced
// the shape the test asserted).

test('formatPushResult names pushed repos, the branch, and marks success', () => {
  const out = formatPushResult({
    ok: true,
    body: {
      pushed: true,
      branch: 'UNA-1',
      pushed_repositories: ['client', 'backend'],
      skipped_repositories: [],
      failed_repositories: [],
    },
  }, 'UNA-1');
  assert.equal(out.title, 'Pushed (UNA-1)');
  assert.equal(out.kind, 'success');
  assert.match(out.message, /pushed 2 repo\(s\) to branch UNA-1: client, backend/);
});

test('formatPushResult: nothing pushed reads "Nothing to push", not a misleading "Pushed"', () => {
  const out = formatPushResult({
    ok: true,
    body: {
      pushed: false,
      branch: 'UNA-2742',
      pushed_repositories: [],
      skipped_repositories: [{ repository_id: 'admin-backend', reason: 'nothing to push' }],
      failed_repositories: [],
    },
  }, 'UNA-2742');
  assert.equal(out.title, 'Nothing to push (UNA-2742)');
  assert.equal(out.kind, 'info');
  assert.match(out.message, /admin-backend: nothing to push/);
  assert.doesNotMatch(out.message, /no action/);
});

test('formatPushResult: all-failed push is an error toast that lists the repos', () => {
  const out = formatPushResult({
    ok: true,
    body: {
      pushed: false,
      branch: 'UNA-2742',
      pushed_repositories: [],
      skipped_repositories: [],
      failed_repositories: [{ repository_id: 'kafka-connect-elasticsearch', error: '403 no push access' }],
    },
  }, 'UNA-2742');
  assert.equal(out.title, 'Push failed (UNA-2742)');
  assert.equal(out.kind, 'error');
  assert.match(out.message, /✗ kafka-connect-elasticsearch: 403 no push access/);
});

test('formatPushResult: partial (some pushed, some failed) downgrades to a warning', () => {
  const out = formatPushResult({
    ok: true,
    body: {
      pushed: true,
      branch: 'UNA-9',
      pushed_repositories: ['client'],
      skipped_repositories: [],
      failed_repositories: [{ repository_id: 'lib', error: 'denied' }],
    },
  }, 'UNA-9');
  assert.equal(out.title, 'Pushed (UNA-9)');
  assert.equal(out.kind, 'warning');
  assert.match(out.message, /pushed 1 repo\(s\) to branch UNA-9: client/);
  assert.match(out.message, /✗ lib: denied/);
});

test('formatPushResult falls back to a generic title without a task id', () => {
  const out = formatPushResult({
    ok: true,
    body: { pushed: false, pushed_repositories: [], skipped_repositories: [], failed_repositories: [] },
  });
  assert.equal(out.title, 'Nothing to push');
  assert.match(out.message, /already in sync/);
});

test('formatPushResult surfaces a request failure', () => {
  const out = formatPushResult({ ok: false, error: 'boom' }, 'UNA-1');
  assert.equal(out.kind, 'error');
  assert.equal(typeof out.title, 'string');
  assert.equal(out.title.length > 0, true);
});

// Three previously-duplicated formatters now share building blocks
// here. The tests pin the bullet shape + classification rules so
// the next button to land can rely on them.

test('formatRequestFailure surfaces error string with the caller-supplied title', () => {
  const out = formatRequestFailure(
    { ok: false, error: 'connect timeout' },
    'Pull failed',
  );
  assert.equal(out.title, 'Pull failed');
  assert.equal(out.kind, 'error');
  assert.match(out.message, /connect timeout/);
});

test('formatRequestFailure falls back to body.error and finally to "unknown error"', () => {
  const withBodyError = formatRequestFailure(
    { ok: false, body: { error: 'rate limited' } },
    'X failed',
  );
  assert.match(withBodyError.message, /rate limited/);
  const noClue = formatRequestFailure({ ok: false }, 'X failed');
  assert.equal(noClue.message, 'unknown error');
});

test('formatPushSummary uses count_only mode for the update-source toast', () => {
  const out = formatPushSummary(
    { pushed_repositories: ['a', 'b'] },
    { pushedSummary: 'count_only' },
  );
  assert.equal(out, '✓ pushed 2 repo(s) to remote');
});

test('formatPushSummary uses with_ids mode for the finish toast', () => {
  const out = formatPushSummary(
    { pushed_repositories: ['client', 'server'] },
    { pushedSummary: 'with_ids' },
  );
  assert.equal(out, '✓ pushed 2 repo(s): client, server');
});

test('formatPushSummary surfaces failures with a "; "-joined detail string', () => {
  const out = formatPushSummary({
    failed_repositories: [
      { repository_id: 'a', error: 'auth' },
      { repository_id: 'b', error: 'net' },
    ],
  });
  assert.equal(out, '✗ push failed: a: auth; b: net');
});

test('formatPushSummary returns null when nothing happened', () => {
  assert.equal(formatPushSummary({}), null);
});

test('formatPullResult: success path lists per-repo commit counts and titles "Pulled"', () => {
  const out = formatPullResult({
    ok: true,
    body: {
      pulled_repositories: [
        { repository_id: 'client', commits_pulled: 3 },
        { repository_id: 'server', commits_pulled: 1 },
      ],
      skipped_repositories: [],
      failed_repositories: [],
    },
  });
  assert.equal(out.title, 'Pulled');
  assert.equal(out.kind, 'success');
  assert.match(out.message, /✓ client: pulled 3 commit\(s\)/);
  assert.match(out.message, /✓ server: pulled 1 commit\(s\)/);
});

test('formatPullResult: dirty-tree skip is a warning bullet, not a failure', () => {
  const out = formatPullResult({
    ok: true,
    body: {
      pulled_repositories: [],
      skipped_repositories: [{
        repository_id: 'client',
        reason: 'dirty_working_tree',
        detail: 'has uncommitted edits',
      }],
      failed_repositories: [],
    },
  });
  assert.equal(out.kind, 'warning');
  assert.match(out.message, /⚠ client: has uncommitted edits/);
});

test('formatPullResult: already-in-sync renders as "nothing to pull"', () => {
  const out = formatPullResult({
    ok: true,
    body: {
      pulled_repositories: [],
      skipped_repositories: [{ repository_id: 'client', reason: 'already_in_sync' }],
      failed_repositories: [],
    },
  });
  assert.match(out.message, /• client: nothing to pull/);
});

test('formatPullResult: any failure with no successes is an error toast', () => {
  const out = formatPullResult({
    ok: true,
    body: {
      pulled_repositories: [],
      skipped_repositories: [],
      failed_repositories: [{ repository_id: 'client', error: 'fetch refused' }],
    },
  });
  assert.equal(out.kind, 'error');
  assert.equal(out.title, 'Nothing to pull');
  assert.match(out.message, /✗ client: fetch refused/);
});

test('formatPullResult: partial success (some pulled + some failed) downgrades to warning', () => {
  const out = formatPullResult({
    ok: true,
    body: {
      pulled_repositories: [{ repository_id: 'client', commits_pulled: 2 }],
      skipped_repositories: [],
      failed_repositories: [{ repository_id: 'server', error: 'fetch refused' }],
    },
  });
  assert.equal(out.kind, 'warning');
  assert.equal(out.title, 'Pull partially completed');
});

test('formatPullResult: empty workspace shows the friendly placeholder', () => {
  const out = formatPullResult({ ok: true, body: {} });
  assert.match(out.message, /no repositories in workspace/);
});

test('formatUpdateSourceResult: pushed-and-updated shows both lines', () => {
  const out = formatUpdateSourceResult({
    ok: true,
    body: {
      updated: true,
      pushed: { pushed_repositories: ['client'] },
      updated_repositories: ['client'],
    },
  });
  assert.equal(out.title, 'Source updated');
  assert.match(out.message, /✓ pushed 1 repo\(s\) to remote/);
  assert.match(out.message, /✓ source updated for 1 repo\(s\): client/);
});

test('formatUpdateSourceResult: per-repo warnings get the right marker', () => {
  const out = formatUpdateSourceResult({
    ok: true,
    body: {
      updated: true,
      pushed: {},
      updated_repositories: ['client'],
      warnings: [
        { warning: 'stash reapplied with conflicts', stash_conflict: true },
        { warning: 'note something else', stash_conflict: false },
      ],
    },
  });
  assert.match(out.message, /⚠ stash reapplied with conflicts/);
  assert.match(out.message, /• note something else/);
});

test('formatFinishResult: full happy path includes push, PR, and move-to-review lines', () => {
  const out = formatFinishResult({
    ok: true,
    body: {
      finished: true,
      pushed: { pushed_repositories: ['client'] },
      pull_request: {
        created_pull_requests: [{ url: 'https://example/pr/1' }],
      },
      moved_to_review: true,
    },
  });
  assert.equal(out.title, 'Done — task finalised');
  assert.match(out.message, /✓ pushed 1 repo\(s\): client/);
  assert.match(out.message, /✓ opened 1 pull request\(s\): https:\/\/example\/pr\/1/);
  assert.match(out.message, /✓ ticket moved to In Review/);
});

test('formatFinishResult: title includes task id when supplied', () => {
  // The toast title used to be just "Done — task finalised" — when an
  // operator had several tabs mid-flow it was easy to lose track of
  // which task the toast was for. Passing ``taskId`` interpolates it
  // into the title.
  const out = formatFinishResult(
    {
      ok: true,
      body: {
        finished: true,
        pushed: { pushed_repositories: ['client'] },
        pull_request: { skipped_existing: ['client'] },
        moved_to_review: true,
      },
    },
    'UNA-2536',
  );
  assert.equal(out.title, 'Done — task finalised (UNA-2536)');
});

test('formatFinishResult: omitting task id keeps the bare title', () => {
  const out = formatFinishResult({
    ok: true,
    body: {
      finished: true,
      pushed: { pushed_repositories: [] },
      pull_request: {},
      moved_to_review: true,
    },
  });
  assert.equal(out.title, 'Done — task finalised');
});

test('formatFinishResult: missing move-to-review surfaces the reason', () => {
  const out = formatFinishResult({
    ok: true,
    body: {
      finished: false,
      pushed: { pushed_repositories: [] },
      pull_request: { skipped_existing: ['client'] },
      moved_to_review: false,
      move_error: 'state field locked',
    },
  });
  assert.equal(out.title, 'Done — partial completion');
  assert.match(out.message, /✗ ticket did NOT move to In Review: state field locked/);
});



test('formatMergeResult: a blocked repo shows its real reason, not "already up to date"', () => {
    const out = formatMergeResult({
      ok: true,
      body: {
        merged_repositories: [],
        skipped_repositories: [{
          repository_id: 'backend',
          reason: 'wrong_branch_checked_out',
          detail: "workspace is on 'master', expected 'feat/x' — checkout first",
        }],
        failed_repositories: [],
      },
    });
    assert.equal(out.title, 'Merge blocked');
    assert.equal(out.kind, 'warning');
    assert.ok(out.message.includes("workspace is on 'master'"));
    assert.ok(!out.message.includes('already up to date'));
  });

test('formatMergeResult: genuinely up-to-date repos keep the calm info toast', () => {
    const out = formatMergeResult({
      ok: true,
      body: {
        merged_repositories: [],
        skipped_repositories: [
          { repository_id: 'client', reason: 'already_up_to_date', detail: '' },
        ],
        failed_repositories: [],
      },
    });
    assert.equal(out.title, 'Nothing to merge');
    assert.equal(out.kind, 'info');
    assert.ok(out.message.includes('client: already up to date'));
  });

test('formatMergeResult: a merge that first saved uncommitted work says so', () => {
    const out = formatMergeResult({
      ok: true,
      body: {
        merged_repositories: [{
          repository_id: 'backend', commits_merged: 3,
          default_branch: 'master', wip_committed: true,
        }],
        skipped_repositories: [],
        failed_repositories: [],
      },
    });
    assert.equal(out.title, 'Default branch merged');
    assert.ok(out.message.includes('merged 3 commit(s) from master'));
    assert.ok(out.message.includes('WIP commit'));
  });

test('formatMergeConflicts NAMES each conflicted repository with its file count', () => {
  const out = formatMergeConflicts([
    { repository_id: 'ob-love-admin-backend', default_branch: 'master',
      conflicted_files: ['a.py', 'b.py', 'c.py'] },
    { repository_id: 'ob-love-admin-client', default_branch: 'master',
      conflicted_files: ['x.js'] },
  ], { chatDelivered: true });

  assert.equal(out.kind, 'warning');
  assert.equal(out.title, 'Merged master — conflicts to resolve');
  // Each repo is named, with correct singular/plural counts.
  assert.ok(out.message.includes('ob-love-admin-backend: 3 conflicted files'));
  assert.ok(out.message.includes('ob-love-admin-client: 1 conflicted file'));
  assert.ok(out.message.includes('Asked Claude in the chat'));
});

test('formatMergeConflicts falls back gracefully when repos differ / chat unreachable', () => {
  const out = formatMergeConflicts([
    { repository_id: 'backend', default_branch: 'master', conflicted_files: ['a'] },
    { repository_id: 'client', default_branch: 'main', conflicted_files: ['b'] },
  ], { chatDelivered: false });

  // Mixed default branches → generic branch phrasing in the title.
  assert.equal(out.title, 'Merged the default branch — conflicts to resolve');
  assert.ok(out.message.includes('backend: 1 conflicted file'));
  assert.ok(out.message.includes('client: 1 conflicted file'));
  assert.ok(out.message.includes("Couldn't reach the chat"));
});

test('formatMergeConflicts includes the task id in the title when given', () => {
  // Regression: this toast (like every other global/backgrounded-task
  // toast) named the repo but never the task — in a multi-task operator
  // session there was no way to tell which tab's merge just conflicted.
  const out = formatMergeConflicts(
    [{ repository_id: 'ob-love-admin-backend', default_branch: 'master',
       conflicted_files: ['a.py', 'b.py', 'c.py'] }],
    { chatDelivered: true, taskId: 'UNA-2536' },
  );
  assert.equal(out.title, 'Merged master — conflicts to resolve (UNA-2536)');
});

test('formatMergeConflicts omits the task-id suffix when no taskId is given', () => {
  const out = formatMergeConflicts(
    [{ repository_id: 'backend', default_branch: 'master', conflicted_files: ['a'] }],
    { chatDelivered: true },
  );
  assert.equal(out.title, 'Merged master — conflicts to resolve');
});

test('a blocked branch switch is flagged with the warning marker', () => {
  // ``blocked`` means git refused and NOTHING changed — the operator has
  // to look. A bullet would read as routine.
  const out = formatUpdateSourceResult({
    ok: true,
    body: {
      updated: false,
      pushed: {},
      updated_repositories: [],
      warnings: [
        { warning: 'local changes would be overwritten', blocked: true },
      ],
    },
  });
  assert.match(out.message, /⚠ local changes would be overwritten/);
});

test('the pre-upgrade stash_conflict shape is still flagged', () => {
  // An in-flight response from a kato that has not restarted yet must not
  // be silently downgraded to a bullet.
  const out = formatUpdateSourceResult({
    ok: true,
    body: {
      updated: true,
      pushed: {},
      updated_repositories: [],
      warnings: [{ warning: 'old shape', stash_conflict: true }],
    },
  });
  assert.match(out.message, /⚠ old shape/);
});

test('a carried-changes note is routine, not a warning', () => {
  const out = formatUpdateSourceResult({
    ok: true,
    body: {
      updated: true,
      pushed: {},
      updated_repositories: ['client'],
      warnings: [{ warning: 'changes carried across untouched', blocked: false }],
    },
  });
  assert.match(out.message, /• changes carried across untouched/);
});

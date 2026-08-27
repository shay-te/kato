// Toast-message formatters for the session-header action buttons.
//
// Pulled out of SessionHeader.jsx because three buttons (Push,
// Pull, Update source, Done) each need to translate a backend
// payload of the shape ``{pushed_repositories, skipped_repositories,
// failed_repositories}`` into a per-repo bullet list. Before this
// extraction the three formatters had drifted slightly — same
// shape, three different join orders, three different "nothing
// happened" stubs — and the next button to land would have made it
// four. One module, one set of building blocks.
//
// Pure functions only, no React. Component code stays a render-only
// JSX file (see AGENTS.md "no logic inside JSX").

import { apiErrorMessage } from '../utils/apiError.js';
import { countNoun } from '../utils/pluralize.js';

// Render the "✓ pushed N repo(s) / • push skipped / ✗ push failed"
// line that both the ``Done`` and ``Update source`` toasts emit
// for the push step. The shape of ``pushed`` is the same payload
// kato's ``push_task`` returns.
export function formatPushSummary(pushed, options = {}) {
  const { pushedSummary } = options;
  const pushedRepositories = pushed.pushed_repositories || [];
  const skippedRepositories = pushed.skipped_repositories || [];
  const failedRepositories = pushed.failed_repositories || [];
  if (pushedRepositories.length) {
    if (pushedSummary === 'count_only') {
      return `✓ pushed ${pushedRepositories.length} repo(s) to remote`;
    }
    return `✓ pushed ${pushedRepositories.length} repo(s): ${pushedRepositories.join(', ')}`;
  }
  if (skippedRepositories.length) {
    // Name the repo and the backend's REASON. This used to claim
    // "already in sync" for every skip, which is the one sentence an
    // operator reads as "kato pushed my work" — including when the
    // clone was sitting on master with the work never committed to the
    // task branch at all.
    return skippedRepositories
      .map((entry) => (
        `• push skipped ${entry.repository_id}: ${entry.reason || 'nothing to push'}`
      ))
      .join('\n');
  }
  if (failedRepositories.length) {
    const errs = failedRepositories
      .map((r) => `${r.repository_id}: ${r.error}`).join('; ');
    return `✗ push failed: ${errs}`;
  }
  return null;
}

// Format an arbitrary list of per-repo failure entries — used by
// both the pull and update-source flows. Each entry is
// ``{repository_id, error}``. Returns one bullet per entry.
// The title has to carry the verdict on its own: it is the only part still
// visible once the toast is glanced at rather than read.
function updateSourceTitle(anyUpdated, problemCount) {
  if (problemCount > 0) {
    return anyUpdated
      ? `Source partially updated — ${countNoun(problemCount, 'problem')}`
      : `Source not updated — ${countNoun(problemCount, 'problem')}`;
  }
  return anyUpdated ? 'Source updated' : 'Source not updated';
}

// Raw git output, indented so it reads as DETAIL of the line above rather
// than as more repositories. Unindented, a multi-line git error ran straight
// into the next repo's entry with nothing marking the boundary.
function indentDetail(text) {
  return String(text || '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => `      ${line}`);
}

export function formatFailedLines(failed) {
  return (failed || []).map((entry) => `✗ ${entry.repository_id}: ${entry.error}`);
}

// Standard "request-level failure" toast (the fetch itself bombed
// or the server returned !ok). Used as the bail-out branch for
// every formatter so they share one place to surface
// transport-level errors.
export function formatRequestFailure(result, fallbackTitle) {
  // Canonical precedence (body.error → result.error → fallback) via the
  // shared apiErrorMessage util, so a request failure surfaces the same
  // error text as every other toast in the app (DUP-11: aligned).
  return {
    title: fallbackTitle,
    kind: 'error',
    message: apiErrorMessage(result, 'unknown error'),
  };
}

// Build the toast for ``POST /pull``. Mirrors the shape kato's
// ``pull_task`` returns: per-repo pulled / skipped / failed lists.
export function formatPullResult(result) {
  if (!result || !result.ok) {
    return formatRequestFailure(result, 'Pull failed');
  }
  const body = result.body || {};
  const pulled = body.pulled_repositories || [];
  const skipped = body.skipped_repositories || [];
  const failed = body.failed_repositories || [];
  const lines = [];
  for (const entry of pulled) {
    const count = Number(entry.commits_pulled || 0);
    lines.push(`✓ ${entry.repository_id}: pulled ${count} commit(s)`);
  }
  for (const entry of skipped) {
    lines.push(formatPullSkipLine(entry));
  }
  lines.push(...formatFailedLines(failed));
  if (lines.length === 0) {
    lines.push('• no repositories in workspace');
  }
  return {
    title: pulled.length
      ? (failed.length ? 'Pull partially completed' : 'Pulled')
      : 'Nothing to pull',
    kind: classifyPullKind({ pulled, skipped, failed }),
    message: lines.join('\n'),
  };
}

function formatPullSkipLine(entry) {
  const reason = entry.reason || 'no_change';
  const detail = entry.detail || '';
  if (reason === 'already_in_sync' || reason === 'remote_branch_missing') {
    return `• ${entry.repository_id}: nothing to pull`;
  }
  if (reason === 'dirty_working_tree') {
    return `⚠ ${entry.repository_id}: ${detail || 'dirty working tree'}`;
  }
  return `• ${entry.repository_id}: ${detail || reason}`;
}

function classifyPullKind({ pulled, skipped, failed }) {
  if (failed.length > 0) {
    return pulled.length > 0 ? 'warning' : 'error';
  }
  if (skipped.some((entry) => entry.reason === 'dirty_working_tree')) {
    return 'warning';
  }
  return 'success';
}

// Build the toast for ``POST /update-source``. The shape mixes a
// nested ``pushed`` block (handled by ``formatPushSummary``) with
// per-repo update / warning / skip / fail lists.
export function formatUpdateSourceResult(result) {
  if (!result || !result.ok) {
    return formatRequestFailure(result, 'Update source failed');
  }
  const body = result.body || {};
  const updated = body.updated_repositories || [];
  const failed = body.failed_repositories || [];
  const skipped = body.skipped_repositories || [];
  const warnings = body.warnings || [];
  // ``blocked`` = git refused and nothing changed, so the operator has to
  // decide. ``stash_conflict`` is the old shape, still read so an in-flight
  // response from a pre-upgrade kato is not silently downgraded.
  const blocked = warnings.filter((e) => !!(e.blocked || e.stash_conflict));
  const notes = warnings.filter((e) => !(e.blocked || e.stash_conflict));

  // PROBLEMS FIRST. This report is read to answer one question — "what
  // didn't work?" — and the answer used to sit under a list of successes,
  // with raw git output running into the next repo's line so the boundary
  // between them was invisible.
  const problems = [];
  const problemCount = blocked.length + failed.length;
  if (problemCount > 0) {
    problems.push(
      `⚠ ${problemCount} repo(s) need your attention`,
    );
  }
  for (const entry of failed) {
    problems.push(`  ✗ ${entry.repository_id}`);
    problems.push(...indentDetail(entry.error));
  }
  for (const entry of blocked) {
    const text = String(entry.warning || '').trim();
    if (!text) { continue; }
    const [headline, ...rest] = text.split('\n');
    problems.push(
      `  ⚠ ${entry.repository_id ? `${entry.repository_id}: ` : ''}${headline}`,
    );
    problems.push(...indentDetail(rest.join('\n')));
  }

  const progress = [];
  const pushLine = formatPushSummary(body.pushed || {}, { pushedSummary: 'count_only' });
  if (pushLine) { progress.push(pushLine); }
  if (updated.length) {
    // "repo(s)" not countNoun: the pushed line directly above it says
    // "repo(s)" too, and two neighbouring lines counting the same thing in
    // different words reads as a bug.
    progress.push(
      `✓ source updated for ${updated.length} repo(s): ${updated.join(', ')}`,
    );
  }
  for (const entry of notes) {
    const text = String(entry.warning || '').trim();
    if (text) { progress.push(`• ${text}`); }
  }
  for (const entry of skipped) {
    progress.push(`• skipped ${entry.repository_id}: ${entry.reason}`);
  }
  if (!updated.length && !failed.length && !skipped.length) {
    progress.push('• no source repositories updated');
  }

  const lines = problems.length
    ? [...problems, '', ...progress]
    : progress;
  return {
    title: updateSourceTitle(body.updated, problemCount),
    message: lines.join('\n'),
  };
}

// Build the toast for ``POST /finish``. Three steps (push, PR,
// move-to-review) each get a single line with the failure reason
// inline when something didn't run.
//
// ``taskId`` (optional) is interpolated into the title so the toast
// makes it obvious WHICH task finished — without it the operator
// gets a generic "Done — task finalised" with no anchor, easy to
// confuse when several tabs are mid-flow.
export function formatFinishResult(result, taskId = '') {
  if (!result || !result.ok) {
    return formatRequestFailure(result, 'Finish request failed');
  }
  const body = result.body || {};
  const lines = [];
  const pushLine = formatPushSummary(body.pushed || {}, { pushedSummary: 'with_ids' });
  lines.push(pushLine || `• push: ${body.pushed?.error || 'no action'}`);
  lines.push(formatPullRequestStepLine(body.pull_request || {}));
  if (body.moved_to_review) {
    lines.push('✓ ticket moved to In Review');
  } else {
    lines.push(`✗ ticket did NOT move to In Review: ${body.move_error || 'unknown reason — check kato logs'}`);
  }
  const baseTitle = body.finished ? 'Done — task finalised' : 'Done — partial completion';
  const trimmedTask = String(taskId || '').trim();
  return {
    title: trimmedTask ? `${baseTitle} (${trimmedTask})` : baseTitle,
    message: lines.join('\n'),
  };
}

// Toast for the operator-triggered "Merge default branch" button.
// Lists EVERY repo touched — each merged repo with its commit count +
// source branch, each already-up-to-date repo, each failure — instead of
// a bare count (or the old generic "nothing to merge"), so the operator
// sees exactly which repositories were merged from master. The conflict
// path stays in the component (it has async side effects — it messages
// the agent to resolve); this covers the clean merged / skipped / failed
// / nothing outcomes.
// The conflict toast for the "Merge master" button. NAMES every repository
// that conflicted (+ its conflicted-file count) so the operator knows WHERE
// to look — the old toast said only "N conflicted file(s)" with no repo, so
// in a multi-repo task you couldn't tell which clone needed attention. The
// agent is asked to resolve in the chat separately; ``chatDelivered`` says
// whether that message reached it.
//
// ``taskId`` (optional) is interpolated into the title, same as
// formatFinishResult/formatPushResult — the global toast surfaces for
// backgrounded tasks too, so without it a multi-task operator can't tell
// which tab's merge just conflicted.
export function formatMergeConflicts(conflicted, { chatDelivered, taskId = '' } = {}) {
  const repos = Array.isArray(conflicted) ? conflicted : [];
  const branches = new Set(
    repos.map((r) => String(r.default_branch || '').trim()).filter(Boolean),
  );
  const branch = branches.size === 1 ? [...branches][0] : 'the default branch';
  const lines = repos.map((repo) => {
    const files = Array.isArray(repo.conflicted_files) ? repo.conflicted_files : [];
    const n = files.length;
    const noun = n === 1 ? 'file' : 'files';
    return `⚠ ${repo.repository_id}: ${n} conflicted ${noun}`;
  });
  const tail = chatDelivered
    ? 'Asked Claude in the chat to resolve them.'
    : "Couldn't reach the chat — resolve manually or message Claude yourself.";
  const body = lines.length ? `${lines.join('\n')}\n\n${tail}` : tail;
  const trimmedTask = String(taskId || '').trim();
  const baseTitle = `Merged ${branch} — conflicts to resolve`;
  return {
    kind: 'warning',
    title: trimmedTask ? `${baseTitle} (${trimmedTask})` : baseTitle,
    message: body,
  };
}

export function formatMergeResult(result, taskId = '') {
  if (!result || !result.ok) {
    return formatRequestFailure(result, 'Merge failed');
  }
  const body = result.body || {};
  const merged = body.merged_repositories || [];
  const skipped = body.skipped_repositories || [];
  const failed = body.failed_repositories || [];

  const lines = [];
  for (const entry of merged) {
    const count = Number(entry.commits_merged || 0);
    const branch = String(entry.default_branch || '').trim() || 'default branch';
    const wip = entry.wip_committed
      ? ' (uncommitted work saved as a WIP commit first)'
      : '';
    lines.push(`✓ ${entry.repository_id}: merged ${count} commit(s) from ${branch}${wip}`);
  }
  // A skip is only "already up to date" when the backend SAYS so — every
  // other reason (wrong branch, fetch failure, …) must be shown, not
  // masked. Masking was the "clicks Merge master, sees 'already up to
  // date' forever" bug: the merge was being refused and nobody knew why.
  let blockedSkips = 0;
  for (const entry of skipped) {
    if ((entry.reason || '') === 'already_up_to_date') {
      lines.push(`• ${entry.repository_id}: already up to date`);
    } else {
      blockedSkips += 1;
      const why = String(entry.detail || entry.reason || 'skipped').trim();
      lines.push(`⚠ ${entry.repository_id}: ${why}`);
    }
  }
  lines.push(...formatFailedLines(failed));
  if (lines.length === 0) {
    lines.push('• no repositories eligible to merge');
  }

  let title;
  let kind;
  if (merged.length) {
    title = failed.length ? 'Default branch merged (partial)' : 'Default branch merged';
    kind = failed.length ? 'warning' : 'success';
  } else if (failed.length) {
    title = 'Merge failed';
    kind = 'error';
  } else if (blockedSkips) {
    title = 'Merge blocked';
    kind = 'warning';
  } else {
    // Every repo already contained the default branch — list them so the
    // operator sees what was checked, not a vague "nothing to merge".
    title = 'Nothing to merge';
    kind = 'info';
  }
  const trimmedTask = String(taskId || '').trim();
  return {
    title: trimmedTask ? `${title} (${trimmedTask})` : title,
    kind,
    message: lines.join('\n'),
  };
}

// Toast for the operator-triggered Push button (POST /push).
//
// IMPORTANT: ``/push`` returns the FLAT ``push_task`` payload —
// ``{pushed: <bool>, branch, pushed_repositories, skipped_repositories,
// failed_repositories}`` — NOT the nested ``{pushed: {...}}`` shape the
// finish toast carries. Reading ``body.pushed`` as a dict here was the
// "Pushed · push: no action" bug: ``body.pushed`` is a boolean, so the
// summary always saw empty lists and fell through to "no action" no
// matter what actually pushed. This reads the flat lists directly,
// names each repo, and reports the branch so the operator can see
// exactly what moved and where. Returns its own ``kind`` (success /
// warning / error / info) so the caller doesn't re-derive it off the
// wrong shape.
export function formatPushResult(result, taskId = '') {
  if (!result || !result.ok) {
    return formatRequestFailure(result, 'Push request failed');
  }
  const body = result.body || {};
  const pushed = body.pushed_repositories || [];
  const skipped = body.skipped_repositories || [];
  const failed = body.failed_repositories || [];
  const branch = String(body.branch || '').trim();
  const onBranch = branch ? ` to branch ${branch}` : '';
  const trimmedTask = String(taskId || '').trim();
  const suffix = trimmedTask ? ` (${trimmedTask})` : '';

  const lines = [];
  // Repos the push pulled in from the task's tags on its way here (a
  // repo added mid-task). Worth its own line: the operator added it
  // somewhere else entirely and this is the confirmation it is now part
  // of the task's workspace.
  const synced = body.synced_repositories || [];
  if (synced.length) {
    lines.push(`+ added from the task's tags: ${synced.join(', ')}`);
  }
  if (pushed.length) {
    lines.push(`✓ pushed ${pushed.length} repo(s)${onBranch}: ${pushed.join(', ')}`);
  }
  for (const entry of skipped) {
    lines.push(`• ${entry.repository_id}: ${entry.reason || 'nothing to push'}`);
  }
  lines.push(...formatFailedLines(failed));

  // Title + kind reflect what actually happened — never claim "Pushed"
  // when nothing moved (the misleading original).
  let title;
  let kind;
  if (pushed.length) {
    title = `Pushed${suffix}`;
    kind = failed.length ? 'warning' : 'success';
  } else if (failed.length) {
    title = `Push failed${suffix}`;
    kind = 'error';
  } else {
    title = `Nothing to push${suffix}`;
    kind = 'info';
    if (lines.length === 0) {
      lines.push('• all repos already in sync — nothing to push');
    }
  }
  return { title, kind, message: lines.join('\n') };
}

function formatPullRequestStepLine(pr) {
  const created = pr.created_pull_requests || [];
  const skipped = pr.skipped_existing || [];
  const failed = pr.failed_repositories || [];
  if (created.length) {
    const urls = created.map((r) => r.url || r.repository_id).join(', ');
    return `✓ opened ${created.length} pull request(s): ${urls}`;
  }
  if (skipped.length) {
    return `• PR skipped — already exists for ${skipped.length} repo(s)`;
  }
  if (failed.length) {
    const errs = failed.map((r) => `${r.repository_id}: ${r.error}`).join('; ');
    return `✗ PR failed: ${errs}`;
  }
  return `• pull request: ${pr.error || 'no action'}`;
}
